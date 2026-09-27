"""Page/sheet-preserving ingestion. Extracted content is never marked reviewed automatically."""
import base64, csv, io, hashlib, zipfile
from pathlib import Path
MAX_BYTES=15_000_000

def ingest(name,content_b64=None,text=None):
    if (content_b64 is None)==(text is None):raise ValueError('supply text OR base64 content')
    raw=text.encode() if text is not None else base64.b64decode(content_b64,validate=True)
    if len(raw)>MAX_BYTES:raise ValueError('source exceeds 15MB')
    ext=Path(name).suffix.lower();segments=[];warnings=[]
    if text is not None or ext in ['.txt','.md','.csv','.tsv']:
        decoded=raw.decode('utf-8-sig')
        if ext in ['.csv','.tsv']:
            rows=list(csv.reader(io.StringIO(decoded),delimiter='\t' if ext=='.tsv' else ','))
            segments=[{'locator':'table:1','text':decoded,'table':rows}]
        else:
            chunks=decoded.split('\f')
            segments=[{'locator':f'page:{j+1}','text':v} for j,v in enumerate(chunks)]
        if text is not None:warnings.append('Host-supplied text: original pagination/completeness must be verified by the host.')
    elif ext=='.pdf':
        from pypdf import PdfReader
        pdf=PdfReader(io.BytesIO(raw))
        if pdf.is_encrypted:raise ValueError('encrypted PDF unsupported; provide authorized decrypted source')
        if len(pdf.pages)>200:raise ValueError('PDF exceeds 200 pages')
        for j,p in enumerate(pdf.pages):
            value=p.extract_text() or ''
            segments.append({'locator':f'page:{j+1}','text':value,'needs_ocr':len(value.strip())<30})
        warnings.append('Text extraction only. Scans/charts/table layout require host visual inspection; no OCR accuracy claim.')
    elif ext in ['.xlsx','.docx']:
        z=zipfile.ZipFile(io.BytesIO(raw))
        if sum(x.file_size for x in z.infolist())>50_000_000:raise ValueError('expanded office file too large')
        if ext=='.xlsx':
            import openpyxl
            wb=openpyxl.load_workbook(io.BytesIO(raw),read_only=True,data_only=False)
            cached=openpyxl.load_workbook(io.BytesIO(raw),read_only=True,data_only=True)
            for sheet in wb:
                if sheet.max_row*sheet.max_column>50000:raise ValueError('worksheet too large')
                lines=[];formulas=[]
                for row in sheet:
                    cells=[]
                    for c in row:
                        if c.value is not None:
                            cells.append(f'{c.coordinate}: {c.value}')
                            if c.data_type=='f':formulas.append({'cell':c.coordinate,'formula':c.value,'cached_value':str(cached[sheet.title][c.coordinate].value)})
                    if cells:lines.append(' | '.join(cells))
                segments.append({'locator':'sheet:'+sheet.title,'text':'\n'.join(lines),'formulas':formulas})
            warnings.append('Workbook formulas are preserved, not recalculated. Cached values may be absent or stale.')
        else:
            from xml.etree import ElementTree as ET
            root=ET.fromstring(z.read('word/document.xml'))
            ns={'w':'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
            paras=[''.join(p.itertext()) for p in root.findall('.//w:p',ns)]
            segments=[{'locator':'document:1','text':'\n'.join(paras)}]
            warnings.append('DOCX text has no reliable pagination; inspect embedded images using the host.')
    else:raise ValueError('unsupported source type; use PDF/XLSX/DOCX/CSV/TXT or host-extracted text')
    if len(segments)>200 or sum(len(s['text']) for s in segments)>2_000_000:raise ValueError('extracted source too large')
    for s in segments:s['review_status']='UNREVIEWED';s['review_note']=''
    return {'name':name,'sha256':hashlib.sha256(raw).hexdigest(),'byte_count':len(raw),
            'segments':segments,'warnings':warnings,'original_stored':False,
            'note':'Store original case files in your own document system; this workspace persists extracted evidence and content hash.'}

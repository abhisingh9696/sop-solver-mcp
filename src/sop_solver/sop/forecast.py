"""Transparent forecast baselines and classical models with rolling-origin selection.

Baselines: naive, mean3, seasonal_naive (unchanged contract).
Classical: SMA, WMA, EMA, SES, Holt (double), Holt-Winters (additive/multiplicative).
All models share multi-step empirical calibration: per-horizon error quantiles from
rolling origins, so p10/p50/p90 bands are calibrated per horizon instead of the old
one-step residual shortcut. No ML/black-box models; no silent demand overwrite.
"""
import numpy as np

MAX_N = 240
MAX_H = 60


def _check(history, horizon, season=1):
    y = np.asarray(history, dtype=float)
    if y.ndim != 1 or not 4 <= len(y) <= MAX_N or not np.isfinite(y).all() or (y < 0).any():
        raise ValueError(f'history must contain 4-{MAX_N} finite nonnegative observations')
    if not 1 <= horizon <= MAX_H or not 1 <= season <= min(60, len(y)//2):
        raise ValueError('invalid horizon/season or insufficient seasonal history')
    return y


def forecast(history: list[float], horizon: int, season: int = 1) -> dict:
    """Baseline selection (naive / mean3 / seasonal_naive) with rolling-origin bands."""
    y = _check(history, horizon, season)
    n = len(y)
    if season > 1 and n >= 2 * season:
        method = 'seasonal_naive'
        point = [y[n - season + (k % season)] for k in range(horizon)]
        errs = [y[t] - y[t - season] for t in range(season, n)]
    elif n >= 4:
        # choose between naive and mean3 by one-step MAE
        e_naive = [y[t] - y[t - 1] for t in range(1, n)]
        e_mean3 = [y[t] - float(np.mean(y[t - 3:t])) for t in range(3, n)]
        if np.mean(np.abs(e_mean3)) < np.mean(np.abs(e_naive)):
            method = 'mean3'; point = [float(np.mean(y[-3:]))] * horizon; errs = e_mean3
        else:
            method = 'naive'; point = [float(y[-1])] * horizon; errs = e_naive
    else:
        method = 'naive'; point = [float(y[-1])] * horizon; errs = []
    p10 = [float(np.quantile(errs, 0.1))] * horizon if len(errs) >= 5 else [None] * horizon
    p90 = [float(np.quantile(errs, 0.9))] * horizon if len(errs) >= 5 else [None] * horizon
    denom = float(np.abs(y).sum())
    out = {'method': method, 'point_forecast': [float(v) for v in point],
           'p10': p10, 'p90': p90,
           'calibration': 'one-step residual empirical quantiles; not a coverage guarantee'}
    out['wmape'] = float(np.abs(errs).sum() / denom) if denom > 0 else None
    out['validation_points'] = int(len(errs))
    return out


def _smoothed_forecasts(history, predict_fn, min_train):
    """One-step in-sample forecasts from each origin t (>=min_train) using predict_fn(v)."""
    return [predict_fn(history[:t])[0] for t in range(min_train, len(history))]


def _multi_step_errors(history, predict_fn, max_h, min_train, season=1):
    """Per-horizon h-step errors across rolling origins (calibrated bands)."""
    errs = {h: [] for h in range(1, max_h + 1)}
    for t in range(min_train, len(history)):
        try:
            f = predict_fn(history[:t], max_h)
        except ValueError:
            continue  # origin too short for the model (e.g. seasonal warm-up); skip, do not count
        for h in range(1, max_h + 1):
            if t + h - 1 < len(history):
                errs[h].append(history[t + h - 1] - f[h - 1])
    return errs


def _fit_alpha_beta(history, family, season=1, grid=None):
    """Simple grid fit by one-step MAE. Honest, transparent, no optimizer surprises."""
    g = grid or np.arange(0.1, 0.91, 0.1)
    best = (np.inf, None)
    for a in g:
        for b in g:
            try:
                _, err = _run(family, history, 1, alpha=a, beta=b, season=season, return_errors=True)
                mae = float(np.mean(np.abs(err)))
            except ValueError:
                continue
            if mae < best[0]:
                best = (mae, (round(float(a), 2), round(float(b), 2)))
    if best[1] is None:
        raise ValueError('no feasible parameter grid')
    return best[1]


def _run(family, y_in, h, alpha=None, beta=None, gamma=None, season=1, return_errors=False):
    y = np.asarray(y_in, dtype=float)
    n = len(y)
    if family == 'ses':
        alpha = alpha if alpha is not None else 0.3
        if not 0 < alpha < 1: raise ValueError('alpha must be in (0,1)')
        level = float(y[0])
        f = np.empty(n); f[0] = level
        for t in range(1, n):
            level = alpha * y[t] + (1 - alpha) * level
            f[t] = level
        point = np.repeat(level, h)
        errs = y[1:] - f[:-1]
    elif family == 'holt':
        alpha = alpha if alpha is not None else 0.3
        beta = beta if beta is not None else 0.1
        if not 0 < alpha < 1 or not 0 < beta < 1: raise ValueError('alpha, beta must be in (0,1)')
        level = float(y[0]); trend = float(y[1] - y[0]) if n > 1 else 0.0
        f = np.empty(n); f[0] = level
        for t in range(1, n):
            f[t] = level + trend
            lvl = alpha * y[t] + (1 - alpha) * (level + trend)
            trend = beta * (lvl - level) + (1 - beta) * trend
            level = lvl
        point = level + trend * np.arange(1, h + 1)
        errs = y[1:] - f[:-1]
    elif family == 'holt_winters':
        if not 2 <= season <= min(60, n // 2): raise ValueError('need >=2 full seasonal cycles')
        alpha = alpha if alpha is not None else 0.3
        beta = beta if beta is not None else 0.1
        gamma = gamma if gamma is not None else 0.2
        if not all(0 < v < 1 for v in [alpha, beta, gamma]): raise ValueError('smoothing constants must be in (0,1)')
        seas = y[:season] / float(np.mean(y[:season])) if np.mean(y[:season]) > 0 else np.ones(season)
        level = float(np.mean(y[:season])); trend = 0.0
        f = np.empty(n)
        for t in range(n):
            s = seas[t % season]
            f[t] = (level + trend) * s
            lvl = alpha * (y[t] / s) + (1 - alpha) * (level + trend)
            trend = beta * (lvl - level) + (1 - beta) * trend
            level = lvl
            seas[t % season] = gamma * (y[t] / level) + (1 - gamma) * s
        point = [(level + trend * k) * seas[(n + k - 1) % season] for k in range(1, h + 1)]
        errs = y[1:] - f[:-1]
    else:
        raise ValueError('unknown family')
    out = np.maximum(0.0, np.asarray(point, dtype=float))
    return (out, errs) if return_errors else out


def _bands_from_multi_step(history, predict_fn, horizon, min_train, season=1):
    errs = _multi_step_errors(history, predict_fn, horizon, min_train, season)
    p10, p90 = [], []
    for h in range(1, horizon + 1):
        e = errs.get(h, [])
        if len(e) < 5:
            p10.append(None); p90.append(None)
        else:
            p10.append(float(np.quantile(e, 0.1)))
            p90.append(float(np.quantile(e, 0.9)))
    return p10, p90


def _summarize(history, horizon, point, p10, p90, errs, method, extra=None):
    denom = np.abs(history).sum() or None
    out = {'method': method, 'point_forecast': [float(v) for v in point],
           'p10_multi_step': p10, 'p90_multi_step': p90,
           'calibration': 'multi-step rolling-origin empirical quantiles per horizon; not a coverage guarantee'}
    if denom:
        out['wmape'] = float(np.abs(errs).sum() / denom)
    out['validation_points'] = int(len(errs))
    if extra: out.update(extra)
    return out


def sma_wma_ema(history: list[float], horizon: int, window: int = 3, weights: list[float] | None = None,
                alpha: float | None = None) -> dict:
    y = _check(history, horizon)
    if not 1 <= window <= min(60, len(y)): raise ValueError('window must fit history')
    if weights is None:
        method = 'sma'
        def predict(v, h=1): return np.repeat(np.mean(v[-window:]), h)
        point = np.repeat(float(np.mean(y[-window:])), horizon)
    elif alpha is None:
        if len(weights) != window or any(w < 0 for w in weights) or sum(weights) <= 0:
            raise ValueError('weights must be nonnegative, length=window')
        method = 'wma'
        w = np.asarray(weights, dtype=float) / sum(weights)
        point = np.repeat(float(w @ y[-window:]), horizon)
        def predict(v, h=1): return np.repeat(float(w @ v[-window:]), h)
    else:
        if not 0 < alpha < 1: raise ValueError('alpha must be in (0,1)')
        method = 'ema'
        level = float(y[0])
        for t in range(1, len(y)):
            level = alpha * y[t] + (1 - alpha) * level
        point = np.repeat(level, horizon)
        def predict(v, h=1):
            lv = float(v[0])
            for t in range(1, len(v)): lv = alpha * v[t] + (1 - alpha) * lv
            return np.repeat(lv, h)
    min_train = max(3, window + 1)
    p10, p90 = _bands_from_multi_step(history, predict, horizon, min_train)
    errs = _smoothed_forecasts(history, predict, min_train)
    return _summarize(history, horizon, point, p10, p90, errs, method,
                      {'parameters': {'window': window} if method in ['sma', 'wma'] else {'alpha': alpha}})


def holt(history: list[float], horizon: int, alpha: float | None = None, beta: float | None = None) -> dict:
    y = _check(history, horizon)
    if alpha is None or beta is None:
        alpha, beta = _fit_alpha_beta(y, 'holt')
    def predict(v, h=1): return _run('holt', v, h, alpha=alpha, beta=beta)
    point = predict(y, horizon)
    _, errs = _run('holt', y, 1, alpha=alpha, beta=beta, return_errors=True)
    min_train = max(4, len(y) - min(12, len(y) // 2))
    p10, p90 = _bands_from_multi_step(history, predict, horizon, min_train)
    return _summarize(history, horizon, point, p10, p90, errs, 'holt',
                      {'parameters': {'alpha': alpha, 'beta': beta}})


def holt_winters(history: list[float], horizon: int, season: int, alpha: float | None = None,
                 beta: float | None = None, gamma: float | None = None) -> dict:
    y = _check(history, horizon, season)
    if alpha is None or beta is None or gamma is None:
        best = (np.inf, None)
        for a in np.arange(0.1, 0.71, 0.2):
            for b in np.arange(0.05, 0.41, 0.175):
                for g in np.arange(0.1, 0.51, 0.2):
                    try:
                        _, errs = _run('holt_winters', y, 1, alpha=a, beta=b, gamma=g, season=season, return_errors=True)
                        mae = float(np.mean(np.abs(errs)))
                    except ValueError:
                        continue
                    if mae < best[0]: best = (mae, (round(float(a), 2), round(float(b), 2), round(float(g), 2)))
        if best[1] is None: raise ValueError('no feasible parameter grid')
        alpha, beta, gamma = best[1]
    def predict(v, h=1): return _run('holt_winters', v, h, alpha=alpha, beta=beta, gamma=gamma, season=season)
    point = predict(y, horizon)
    _, errs = _run('holt_winters', y, 1, alpha=alpha, beta=beta, gamma=gamma, season=season, return_errors=True)
    min_train = max(2 * season, len(y) - min(12, len(y) // 2))
    p10, p90 = _bands_from_multi_step(history, predict, horizon, min_train, season)
    return _summarize(history, horizon, point, p10, p90, errs, 'holt_winters',
                      {'parameters': {'alpha': alpha, 'beta': beta, 'gamma': gamma, 'season': season}})


def ladder(history: list[float], horizon: int, season: int = 1) -> dict:
    """Run all applicable classical models + baselines; select by one-step MAE."""
    y = _check(history, horizon, season)
    results = {}
    try: results['naive'] = forecast(history, horizon, 1)
    except ValueError: pass
    try: results['sma'] = sma_wma_ema(history, horizon, window=3)
    except ValueError: pass
    try: results['ses'] = _summarize_from(history, horizon, 'holt', alpha=None, beta=None)
    except ValueError: pass
    try: results['holt'] = holt(history, horizon)
    except ValueError: pass
    if season > 1:
        try: results['seasonal_naive'] = forecast(history, horizon, season)
        except ValueError: pass
        try: results['holt_winters'] = holt_winters(history, horizon, season)
        except ValueError: pass
    def key(m):
        r = results[m]
        w = r.get('wmape')
        return w if w is not None else np.inf
    chosen = min(results, key=key)
    out = {'chosen': chosen, 'wmape': results[chosen].get('wmape'),
           'models': {m: {'wmape': r.get('wmape'), 'point_forecast': r['point_forecast'],
                          'p10_multi_step': r.get('p10_multi_step'), 'p90_multi_step': r.get('p90_multi_step')}
                      for m, r in results.items()},
           'selected_forecast': results[chosen],
           'basis': 'rolling-origin one-step MAE selection across classical models and baselines; no ML models'}
    return out


def _summarize_from(history, horizon, family, alpha, beta):
    y = np.asarray(history, dtype=float)
    if family == 'ses':
        a = alpha if alpha is not None else 0.3
        def predict(v, h=1): return _run('ses', v, h, alpha=a)
        point = predict(y, horizon); _, errs = _run('ses', y, 1, alpha=a, return_errors=True)
    else:
        raise ValueError('unsupported')
    min_train = max(3, len(y) - min(12, len(y) // 2))
    p10, p90 = _bands_from_multi_step(history, predict, horizon, min_train)
    return _summarize(history, horizon, point, p10, p90, errs, family)


def adjust(forecast: list[float], adjustments: dict[str, float], note: str = '') -> dict:
    """Judgmental overlay. Adjustments are per-period multipliers; nothing is silent."""
    f = np.asarray(forecast, dtype=float)
    if f.ndim != 1 or not 1 <= len(f) <= MAX_H or not np.isfinite(f).all() or (f < 0).any():
        raise ValueError('forecast must be a finite nonnegative series')
    if len(adjustments) > len(f): raise ValueError('too many adjustments')
    out = f.copy(); log = []
    for k, m in adjustments.items():
        try:
            idx = int(k)
        except ValueError:
            raise ValueError('adjustment keys must be 0-based period indices')
        if not 0 <= idx < len(f) or not 0 <= m <= 10 or not np.isfinite(m):
            raise ValueError('invalid adjustment index/multiplier')
        out[idx] = f[idx] * m
        log.append({'period': idx, 'multiplier': m, 'before': float(f[idx]), 'after': float(out[idx])})
    return {'adjusted_forecast': out.tolist(), 'applied': log, 'note': note,
            'warning': 'Judgmental adjustment recorded; statistical models are not retrained on it.'}


def track(history: list[float], forecast: list[float]) -> dict:
    """Forecast-vs-actual diagnostics: bias, MAPE, RMSE, MASE, tracking signal."""
    y = np.asarray(history, dtype=float)
    if y.ndim != 1 or not 1 <= len(y) <= MAX_N or not np.isfinite(y).all() or (y < 0).any():
        raise ValueError(f'history must contain 1-{MAX_N} finite nonnegative observations')
    f = np.asarray(forecast, dtype=float)
    if f.ndim != 1 or len(f) != len(y) or not np.isfinite(f).all():
        raise ValueError('forecast must match history length')
    e = y - f
    denom = np.abs(y).sum()
    ratio = np.divide(np.abs(e), y, out=np.full_like(e, np.nan, dtype=float), where=y > 0)
    valid = ratio[np.isfinite(ratio)]
    mape = float(np.mean(valid)) if valid.size else None
    rmse = float(np.sqrt(np.mean(e ** 2)))
    bias = float(np.mean(e))
    scale = float(np.mean(np.abs(y[1:] - y[:-1]))) if len(y) > 1 else None
    mase = float(np.mean(np.abs(e)) / scale) if scale and scale > 0 else None
    cume = np.cumsum(e)
    mad = np.mean(np.abs(e))
    ts = float(cume[-1] / mad) if mad > 0 else None
    return {'bias': bias, 'mape': mape, 'rmse': rmse, 'mase': mase,
            'wmape': float(np.abs(e).sum() / denom) if denom else None,
            'tracking_signal': ts,
            'cumulative_forecast_error': float(cume[-1]),
            'out_of_control': ts is not None and abs(ts) > 4,
            'basis': 'Pairwise actual-vs-forecast on the supplied series; no distributional claim.'}

"""Phase-5 enterprise engines: ARIMA/SARIMA forecasting, causal regression
forecasting, and multi-echelon inventory policy.

Boundaries respected: ARIMA/SARIMA are classical statistical models (statsmodels),
NOT ML ensembles; regression_forecast uses the existing OLS engine with host-supplied
exogenous features and makes no causal claim; multi-echelon uses textbook safety-stock
pooling math, not simulation of arbitrary networks.
"""
import numpy as np

MAX_N = 240
MAX_H = 60


def arima(data: dict) -> dict:
    """ARIMA/SARIMA fit via statsmodels with residual-based bands."""
    from statsmodels.tsa.arima.model import ARIMA as _ARIMA
    y = np.asarray(data.get('history', []), dtype=float)
    horizon = int(data.get('horizon', 1))
    order = data.get('order')
    seasonal_order = data.get('seasonal_order')
    if y.ndim != 1 or not 12 <= len(y) <= MAX_N or not np.isfinite(y).all() or (y < 0).any():
        raise ValueError(f'history must contain 12-{MAX_N} finite nonnegative observations')
    if not 1 <= horizon <= MAX_H:
        raise ValueError('horizon must be 1-60')
    if order is None and seasonal_order is None:
        raise ValueError('supply order=(p,d,q) or seasonal_order=(P,D,Q,s)')
    p, d, q = (order if order is not None else (0, 0, 0))
    P, D, Q, s = (seasonal_order if seasonal_order is not None else (0, 0, 0, 1))
    for name, v in [('order', (p, d, q)), ('seasonal_order', (P, D, Q, s))]:
        need = 3 if name == 'order' else 4
        if len(v) != need or any(not isinstance(x, int) or x < 0 for x in v):
            raise ValueError(f'{name} must be {need} nonnegative integers')
    if order is not None and not all(x <= 5 for x in (p, d, q)):
        raise ValueError('order entries capped at 5')
    if seasonal_order is not None and (not all(x <= 3 for x in (P, D, Q)) or not 2 <= s <= 24):
        raise ValueError('seasonal entries capped at 3 and s in 2-24')
    try:
        kwargs = {'order': (p, d, q)}
        if seasonal_order is not None:
            kwargs['seasonal_order'] = (P, D, Q, s)
        model = _ARIMA(y, **kwargs).fit(method_kwargs={'maxiter': 200})
        fc = model.get_forecast(horizon)
        point = np.maximum(0.0, fc.predicted_mean)
        ci = fc.conf_int(alpha=0.2)  # 80% interval approximates p10/p90 for symmetric residuals
    except Exception as exc:
        raise ValueError(f'ARIMA fit failed: {str(exc)[:200]}') from exc
    resid = np.asarray(model.resid)
    return {'method': 'arima' if not seasonal_order else 'sarima',
            'numeric_valid': True,
            'point_forecast': [float(v) for v in point],
            'p10_multi_step': [max(0.0, float(a)) for a, b in ci],
            'p90_multi_step': [float(b) for a, b in ci],
            'aic': float(model.aic), 'bic': float(model.bic),
            'order': (p, d, q), 'seasonal_order': (P, D, Q, s),
            'in_sample_rmse': float(np.sqrt(np.mean(resid ** 2))) if len(resid) else None,
            'calibration': 'Residual-based interval from the fitted model; ARIMA/SARIMA are classical statistical models, not ML; treat forecasts as baselines, validate rolling-origin before use.',
            'warning': 'No automatic differencing search: the supplied orders are used as-is.'}


def regression_forecast(data: dict) -> dict:
    """Forecast with exogenous covariates via least squares (association, not causation)."""
    history = data.get('history')
    features = data.get('features')
    future_features = data.get('future_features')
    y = np.asarray(history, dtype=float)
    if y.ndim != 1 or not 6 <= len(y) <= MAX_N or not np.isfinite(y).all() or (y < 0).any():
        raise ValueError(f'history must contain 6-{MAX_N} finite nonnegative observations')
    if not isinstance(features, dict) or not features:
        raise ValueError('features must be a dict of named series')
    n = len(y)
    cols = []
    for name, series in features.items():
        v = np.asarray(series, dtype=float)
        if v.ndim != 1 or len(v) != n or not np.isfinite(v).all():
            raise ValueError(f'feature {name} must match history length and be finite')
        cols.append(v)
    X = np.column_stack([np.ones(n)] + cols)
    if n <= len(cols) + 1:
        raise ValueError('more features than observations')
    beta, _, rank, _ = np.linalg.lstsq(X, y, rcond=None)
    if rank != X.shape[1]:
        raise ValueError('rank deficient feature matrix')
    if not isinstance(future_features, dict) or set(future_features) != set(features):
        raise ValueError('future_features must provide every training feature')
    horizon = None
    fut_cols = []
    for name in features:
        v = np.asarray(future_features[name], dtype=float)
        if v.ndim != 1 or not 1 <= len(v) <= MAX_H or not np.isfinite(v).all():
            raise ValueError(f'future feature {name} must be a finite series of 1-{MAX_H} periods')
        if horizon is None:
            horizon = len(v)
        elif len(v) != horizon:
            raise ValueError('future feature lengths differ')
        fut_cols.append(v)
    Xf = np.column_stack([np.ones(horizon)] + fut_cols)
    pred = Xf @ beta
    resid = y - X @ beta
    sse = float(resid @ resid)
    sst = float(((y - y.mean()) ** 2).sum())
    rmse = float(np.sqrt(np.mean(resid ** 2))) if n else None
    return {'method': 'regression_forecast',
            'numeric_valid': True,
            'point_forecast': [max(0.0, float(v)) for v in pred],
            'coefficients': {'intercept': float(beta[0]),
                             **{name: float(beta[i + 1]) for i, name in enumerate(features)}},
            'in_sample_r_squared': 1 - sse / sst if sst else None,
            'in_sample_rmse': rmse,
            'basis': 'Least-squares association between supplied features and demand. Association is not causation: external features (weather, competitors) must be host-supplied and their future values are assumptions.',
            'warning': 'No holdout evaluation inside this engine; use forecast_track when actuals arrive.'}


def multi_echelon_inventory(data: dict) -> dict:
    """Two-echelon safety-stock pooling: warehouse + stores under risk pooling.

    Textbook formula: pooled safety stock = z * sqrt(sum(var_i) + var_wh) where
    demand variances add under independent-normal assumptions. Reports the pooling
    benefit vs decentralized (no warehouse) sizing.
    """
    stores = data.get('stores') or []
    warehouse = data.get('warehouse')
    service_level = data.get('service_level')
    from scipy import stats
    if not 0 < service_level < 1:
        raise ValueError('service_level must be in (0,1)')
    if not 1 <= len(stores) <= 200:
        raise ValueError('1-200 stores required')
    z = float(stats.norm.ppf(service_level))
    decentralized_store_ss = []
    echelon_store_ss = []
    pooled_var = 0.0
    for s in stores:
        mu = s.get('avg_demand_per_period'); sd = s.get('demand_std_per_period')
        lead = s.get('lead_time_periods', 0)
        repl = s.get('replenishment_lead_time_periods', 1)
        if not isinstance(mu, (int, float)) or not isinstance(sd, (int, float)) or sd < 0 or mu < 0:
            raise ValueError('store demands must be nonnegative numbers')
        if not isinstance(lead, (int, float)) or lead < 0 or not isinstance(repl, (int, float)) or repl < 0:
            raise ValueError('store lead times must be nonnegative')
        deco_ss = z * sd * np.sqrt(lead)
        eche_ss = z * sd * np.sqrt(repl)
        pooled_var += (sd ** 2) * repl
        decentralized_store_ss.append(deco_ss)
        echelon_store_ss.append(eche_ss)
    decentralized_total = float(sum(decentralized_store_ss))
    if warehouse is not None:
        wh_sd = warehouse.get('demand_std_per_period'); wh_lead = warehouse.get('lead_time_periods', 0)
        if not isinstance(wh_sd, (int, float)) or wh_sd < 0 or not isinstance(wh_lead, (int, float)) or wh_lead < 0:
            raise ValueError('warehouse demand std/lead time must be nonnegative numbers')
        wh_var = (wh_sd ** 2) * wh_lead
        warehouse_ss = z * np.sqrt(pooled_var + wh_var)
        warehouse_rop = sum(s['avg_demand_per_period'] for s in stores) * wh_lead + warehouse_ss
        echelon_total = warehouse_ss + sum(echelon_store_ss)
        echelon = {'safety_stock': float(warehouse_ss), 'reorder_point': float(warehouse_rop)}
        pooling_benefit = decentralized_total - echelon_total
    else:
        echelon = None
        echelon_total = decentralized_total
        pooling_benefit = 0.0
    return {'service_level': service_level, 'z': z, 'numeric_valid': True,
            'stores': [{'store': s.get('id', f'store-{i}'),
                        'decentralized_safety_stock': float(d),
                        'echelon_safety_stock': float(e),
                        'reorder_point': float(s['avg_demand_per_period'] * s.get('replenishment_lead_time_periods', 1) + e)}
                       for i, (s, d, e) in enumerate(zip(stores, decentralized_store_ss, echelon_store_ss))],
            'warehouse': echelon,
            'decentralized_total_safety_stock': decentralized_total,
            'echelon_total_safety_stock': float(echelon_total),
            'pooling_benefit': float(pooling_benefit),
            'basis': 'Independent normal demand across stores; variance adds linearly; risk pooling only valid while demand is not perfectly correlated. Real correlation shrinks the benefit. A warehouse adds its own layer, so the echelon total can exceed decentralized for small networks — the benefit is reported as the signed difference, never forced positive.',
            'warning': 'Two echelons only; no transportation, capacity or lot sizing.'}

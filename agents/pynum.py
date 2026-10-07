# -*- coding: utf-8 -*-
"""
PYNUM - numpy/scipy yerine gecen mikro sayisal katman (SAF PYTHON).

Neden: market_coherence.py numpy + scipy.optimize.minimize + scipy.special.gammaln
kullaniyordu; Render free plan'da bu paketler kurulu degil (ve kurmak derleme/bellek
sinirini zorluyor). Burada ihtiyac duyulan dort sey var: Poisson pmf vektoru, iki
vektorun dis carpimi (skor izgarasi), izgara toplamlari ve 2-3 parametreli
Nelder-Mead. GRID=13 icin 169 hucre -> saf Python'da maliyeti ihmal edilebilir
(200 iterasyon ~34 bin islem, milisaniyeler).

Dogruluk: math.lgamma == scipy.special.gammaln (ikisi de log-gamma), Nelder-Mead
Nelder & Mead (1965) standart katsayilariyla (alpha=1, gamma=2, rho=0.5, sigma=0.5).
"""
import math


def pois(lam, n):
    """Poisson pmf vektoru: [P(0), P(1), ..., P(n-1)]."""
    lam = max(float(lam), 1e-9)
    ln = math.log(lam)
    return [math.exp(g * ln - lam - math.lgamma(g + 1)) for g in range(n)]


def outer(a, b):
    """Dis carpim -> satir listesi (a[i] * b[j])."""
    return [[x * y for y in b] for x in a]


def normalize(g):
    """Izgarayi toplami 1 olacak sekilde olcekler."""
    t = sum(sum(r) for r in g)
    if t <= 0:
        return g
    return [[v / t for v in r] for r in g]


def tril_sum(g, k=-1):
    """Alt ucgen toplami (k=-1: kosegen HARIC) -> ev sahibi kazanir."""
    return sum(v for i, r in enumerate(g) for j, v in enumerate(r) if j - i <= k)


def triu_sum(g, k=1):
    """Ust ucgen toplami (k=1: kosegen HARIC) -> deplasman kazanir."""
    return sum(v for i, r in enumerate(g) for j, v in enumerate(r) if j - i >= k)


def trace(g):
    """Kosegen toplami -> beraberlik."""
    return sum(r[i] for i, r in enumerate(g) if i < len(r))


def toplam_gol(g):
    """{toplam_gol: olasilik} - Alt/Ust ve gol-bandi pazarlari icin."""
    out = {}
    for i, r in enumerate(g):
        for j, v in enumerate(r):
            out[i + j] = out.get(i + j, 0.0) + v
    return out


def nelder_mead(f, x0, adim=0.25, xatol=1e-3, fatol=1e-8, maxiter=200):
    """Turevsiz simplex minimizasyonu (Nelder & Mead 1965).
    -> {"x": [...], "fun": deger, "iter": n, "success": bool} (scipy.OptimizeResult benzeri)."""
    n = len(x0)
    alpha, gamma, rho, sigma = 1.0, 2.0, 0.5, 0.5
    simplex = [list(x0)]
    for i in range(n):
        p = list(x0)
        p[i] += adim if p[i] == 0 else adim * abs(p[i])
        simplex.append(p)
    deger = [f(p) for p in simplex]
    it = 0
    for it in range(1, maxiter + 1):
        sirali = sorted(range(n + 1), key=lambda i: deger[i])
        simplex = [simplex[i] for i in sirali]
        deger = [deger[i] for i in sirali]
        if (max(max(abs(simplex[i][j] - simplex[0][j]) for j in range(n)) for i in range(1, n + 1)) <= xatol
                and abs(deger[-1] - deger[0]) <= fatol):
            break
        merkez = [sum(p[j] for p in simplex[:-1]) / n for j in range(n)]
        yansima = [merkez[j] + alpha * (merkez[j] - simplex[-1][j]) for j in range(n)]
        fy = f(yansima)
        if deger[0] <= fy < deger[-2]:
            simplex[-1], deger[-1] = yansima, fy
            continue
        if fy < deger[0]:                                   # genisleme
            genis = [merkez[j] + gamma * (yansima[j] - merkez[j]) for j in range(n)]
            fg = f(genis)
            simplex[-1], deger[-1] = (genis, fg) if fg < fy else (yansima, fy)
            continue
        buzulme = [merkez[j] + rho * (simplex[-1][j] - merkez[j]) for j in range(n)]
        fb = f(buzulme)
        if fb < deger[-1]:
            simplex[-1], deger[-1] = buzulme, fb
            continue
        for i in range(1, n + 1):                           # tum simplexi daralt
            simplex[i] = [simplex[0][j] + sigma * (simplex[i][j] - simplex[0][j]) for j in range(n)]
            deger[i] = f(simplex[i])
    en = min(range(n + 1), key=lambda i: deger[i])
    return {"x": simplex[en], "fun": deger[en], "iter": it, "success": it < maxiter}

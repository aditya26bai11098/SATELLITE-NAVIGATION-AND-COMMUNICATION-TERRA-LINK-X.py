"""
TERRA-LINK X - prototype: GIS + simulated GNSS + AI gap analysis + offline-first (DTN-style) reporting.
Run:  pip install streamlit pandas numpy scikit-learn plotly
      streamlit run terra_link_x.py
All data is synthetic (demo only). Complements, never replaces, official emergency systems.
"""
import hashlib, time
import numpy as np, pandas as pd, plotly.express as px, pydeck as pdk, streamlit as st
from sklearn.ensemble import RandomForestRegressor
from sklearn.metrics import r2_score
from sklearn.model_selection import train_test_split

st.set_page_config("TERRA-LINK X", "🛰️", layout="wide")

REGIONS = {  # name: (lat, lon, mean elevation m, terrain roughness 0-1)
    "Himalayan belt (Uttarakhand)": (30.40, 79.30, 2400, 0.85),
    "North-East (Sikkim hills)": (27.40, 88.50, 1800, 0.75),
    "Coastal Odisha (cyclone zone)": (20.30, 86.20, 15, 0.10),
    "Kutch (arid / remote)": (23.70, 69.80, 40, 0.15),
}
INCIDENTS = ["Medical emergency", "Flood / water-logging", "Landslide", "Cyclone damage", "Power/network outage"]


def haversine(lat1, lon1, lat2, lon2):
    p = np.pi / 180
    a = np.sin((lat2 - lat1) * p / 2) ** 2 + np.cos(lat1 * p) * np.cos(lat2 * p) * np.sin((lon2 - lon1) * p / 2) ** 2
    return 12742 * np.arcsin(np.sqrt(a))  # km


# ---------------------------------------------------------------- 1. GIS data (synthetic)
@st.cache_data
def build_region(name, n=45, seed=7):
    lat0, lon0, elev, rough = REGIONS[name]
    rng = np.random.default_rng(seed + len(name))
    d = pd.DataFrame({"village": [f"Village-{i:02d}" for i in range(n)],
                      "lat": lat0 + rng.normal(0, .35, n), "lon": lon0 + rng.normal(0, .35, n)})
    d["elevation"] = np.clip(rng.normal(elev, 300 * rough + 20, n), 0, None)
    d["slope"] = np.clip(rng.normal(30 * rough, 6, n), 0, 60)          # degrees
    d["population"] = rng.integers(150, 4000, n)
    d["hazard"] = np.clip(rng.beta(2, 3, n) + .25 * rough, 0, 1)        # flood/landslide/cyclone exposure
    towers = pd.DataFrame({"lat": lat0 + rng.normal(0, .3, 4), "lon": lon0 + rng.normal(0, .3, 4)})
    health = pd.DataFrame({"name": [f"Health Centre {i+1}" for i in range(5)],
                           "lat": lat0 + rng.normal(0, .3, 5), "lon": lon0 + rng.normal(0, .3, 5)})
    d["dist_tower_km"] = [haversine(r.lat, r.lon, towers.lat.values, towers.lon.values).min() for r in d.itertuples()]
    d["dist_health_km"] = [haversine(r.lat, r.lon, health.lat.values, health.lon.values).min() for r in d.itertuples()]
    # hidden "ground truth" signal quality (0-1) used to simulate field surveys
    d["signal_true"] = np.clip(1 - d.dist_tower_km / 45 - d.slope / 120 + rng.normal(0, .05, n), 0, 1)
    return d, towers, health


# ---------------------------------------------------------------- 2. AI: coverage prediction + transparent priority
FEATS = ["dist_tower_km", "elevation", "slope"]

@st.cache_data
def coverage_model(df):
    """Random-forest estimates signal quality in settlements nobody has surveyed yet."""
    X_tr, X_te, y_tr, y_te = train_test_split(df[FEATS], df.signal_true, test_size=.3, random_state=1)
    m = RandomForestRegressor(200, random_state=1).fit(X_tr, y_tr)
    out = df.copy(); out["signal_pred"] = np.clip(m.predict(df[FEATS]), 0, 1)
    return out, r2_score(y_te, m.predict(X_te)), dict(zip(FEATS, m.feature_importances_))


def priority(df, w):
    """Transparent weighted score: every factor is visible; no black box decides."""
    n = lambda s: (s - s.min()) / (s.max() - s.min() + 1e-9)
    f = pd.DataFrame({"population": n(df.population), "poor_signal": 1 - df.signal_pred,
                      "health_distance": n(df.dist_health_km), "hazard": df.hazard})
    for k in f: f[k] *= w[k]
    out = df.copy(); out["priority"] = (f.sum(axis=1) / sum(w.values())).round(3)
    return out.join(f.add_prefix("c_")).sort_values("priority", ascending=False)


# ---------------------------------------------------------------- 3. GNSS simulation (GPS + NavIC)
def gnss_fix(row, rng):
    """Terrain-dependent fix: steep/high terrain -> fewer satellites, worse HDOP, larger error."""
    sats = int(np.clip(rng.normal(14 - row.slope / 5, 1.2), 3, 18))   # GPS + NavIC in view
    hdop = round(0.8 + row.slope / 12 + rng.uniform(0, .5) + (6 - sats) * .3 * (sats < 6), 2)
    if sats < 4:
        return dict(ok=False, sats=sats, hdop=None, acc_m=None, lat=row.lat, lon=row.lon, mode="Last-known / manual village")
    acc = round(hdop * 4.0, 1)                                         # ~UERE 4 m x HDOP
    dlat, dlon = rng.normal(0, acc / 2, 2) / 111_000
    return dict(ok=True, sats=sats, hdop=hdop, acc_m=acc, lat=row.lat + dlat, lon=row.lon + dlon, mode="GNSS 3D fix")


# ---------------------------------------------------------------- 4. Offline-first store-and-forward queue
def state():
    for k, v in dict(queue=[], delivered=[], rng=np.random.default_rng(3), fix=None).items():
        st.session_state.setdefault(k, v)


def submit(report, online):
    report["checksum"] = hashlib.sha256(str(report).encode()).hexdigest()[:10]   # integrity tag (prototype)
    report["created"] = time.time()
    if online:
        report.update(status="DELIVERED", sync_delay_s=0.0, ack=True); st.session_state.delivered.append(report)
    else:
        report.update(status="PENDING (queued on device)", sync_delay_s=None, ack=False); st.session_state.queue.append(report)


def sync(loss, latency):
    """DTN-style: retry up to 3x on lossy link; delivered reports are acknowledged."""
    rng, keep = st.session_state.rng, []
    for r in st.session_state.queue:
        for attempt in range(1, 4):
            if rng.random() > loss:
                r.update(status=f"DELIVERED (try {attempt})", ack=True,
                         sync_delay_s=round(time.time() - r["created"] + attempt * latency, 1))
                st.session_state.delivered.append(r); break
        else:
            r["status"] = "PENDING (retry later)"; keep.append(r)
    st.session_state.queue = keep


# ---------------------------------------------------------------- UI
state()
st.title("🛰️ TERRA-LINK X")
st.caption("AI-assisted rural connectivity intelligence · offline-first emergency reporting · GIS + GNSS | concept prototype, synthetic data")

with st.sidebar:
    region = st.selectbox("Region", list(REGIONS))
    online = st.toggle("Network ONLINE", True, help="Switch off to simulate a disaster outage")
    st.subheader("Priority weights")
    w = dict(population=st.slider("Population", 0., 1., .30), poor_signal=st.slider("Poor signal", 0., 1., .30),
             health_distance=st.slider("Distance to health", 0., 1., .20), hazard=st.slider("Hazard exposure", 0., 1., .20))
    st.subheader("Link model")
    loss = st.slider("Packet loss", 0., .9, .3); latency = st.slider("Link latency (s)", 0., 30., 5.)

base, towers, health = build_region(region)
data, r2, imp = coverage_model(base)
ranked = priority(data, w)

tab1, tab2, tab3, tab4 = st.tabs(["🗺️ GIS & AI gap map", "📡 GNSS + report", "📬 Queue & sync", "📊 Metrics"])

with tab1:
    c1, c2, c3 = st.columns(3)
    c1.metric("Settlements mapped", len(data)); c2.metric("Avg predicted signal", f"{data.signal_pred.mean():.2f}")
    c3.metric("Model R² (held-out)", f"{r2:.2f}")
    show = ranked.copy()
    show["color"] = show.priority.apply(lambda p: [int(255 * p), int(200 * (1 - p)), 60, 200])
    layers = [pdk.Layer("ScatterplotLayer", show, get_position="[lon, lat]", get_fill_color="color",
                        get_radius="600 + population / 3", pickable=True),
              pdk.Layer("ScatterplotLayer", towers, get_position="[lon, lat]", get_fill_color=[40, 90, 255], get_radius=1500),
              pdk.Layer("ScatterplotLayer", health, get_position="[lon, lat]", get_fill_color=[255, 255, 255], get_radius=1200,
                        get_line_color=[0, 150, 0], stroked=True, line_width_min_pixels=3)]
    lat0, lon0 = REGIONS[region][:2]
    st.pydeck_chart(pdk.Deck(layers=layers, initial_view_state=pdk.ViewState(latitude=lat0, longitude=lon0, zoom=8),
                             tooltip={"text": "{village}\nPriority {priority}\nSignal {signal_pred}\nPop {population}"}))
    st.caption("Red = high connectivity-improvement priority · blue = towers · green ring = health centres")
    l, r = st.columns([3, 2])
    l.subheader("Top priority settlements (explainable)")
    l.dataframe(ranked[["village", "priority", "population", "signal_pred", "dist_health_km", "hazard",
                        "c_population", "c_poor_signal", "c_health_distance", "c_hazard"]].head(10).round(2), hide_index=True)
    r.subheader("What drives predicted coverage?")
    r.plotly_chart(px.bar(x=list(imp.values()), y=list(imp), orientation="h", labels={"x": "importance", "y": ""}),
                   use_container_width=True)

with tab2:
    a, b = st.columns(2)
    with a:
        v = st.selectbox("Reporter location (village)", data.village)
        row = data[data.village == v].iloc[0]
        if st.button("📡 Acquire GNSS fix"):
            st.session_state.fix = gnss_fix(row, st.session_state.rng)
        fix = st.session_state.fix
        if fix:
            if fix["ok"]:
                st.success(f"{fix['mode']} · {fix['sats']} sats (GPS+NavIC) · HDOP {fix['hdop']} · ±{fix['acc_m']} m")
            else:
                st.warning(f"Only {fix['sats']} satellites visible → fallback: {fix['mode']}")
        inc = st.selectbox("Incident type", INCIDENTS)
        sev = st.select_slider("Severity", ["Low", "Medium", "High", "Critical"], "High")
        consent = st.checkbox("Reporter consents to share contact details", True)
        if st.button("🚨 Submit report", type="primary"):
            f = fix or gnss_fix(row, st.session_state.rng)
            fac = health.assign(d=haversine(f["lat"], f["lon"], health.lat.values, health.lon.values)).nsmallest(1, "d").iloc[0]
            submit(dict(village=v, incident=inc, severity=sev, lat=round(f["lat"], 5), lon=round(f["lon"], 5),
                        gnss=f["mode"], accuracy_m=f["acc_m"], consent=consent, nearest_facility=fac["name"],
                        facility_km=round(fac["d"], 1), eta_min=round(fac["d"] / max(8, 40 - row.slope) * 60)), online)
            st.toast("Delivered" if online else "Offline → queued on device")
    with b:
        if fix:
            pts = pd.DataFrame([dict(lat=row.lat, lon=row.lon, c=[0, 200, 0, 200], r=60),
                                dict(lat=fix["lat"], lon=fix["lon"], c=[255, 0, 0, 220], r=max(fix["acc_m"] or 300, 40))])
            st.pydeck_chart(pdk.Deck(layers=[pdk.Layer("ScatterplotLayer", pts, get_position="[lon, lat]",
                                                       get_fill_color="c", get_radius="r")],
                                     initial_view_state=pdk.ViewState(latitude=row.lat, longitude=row.lon, zoom=14)))
            st.caption("Green = true position · red = GNSS fix with error radius")
        else:
            st.info("Acquire a GNSS fix to see the position and error circle. Steeper terrain → fewer satellites → larger error.")

with tab3:
    st.subheader("Network: " + ("🟢 ONLINE" if online else "🔴 OFFLINE (reports are queued)"))
    q = st.session_state.queue
    st.write(f"**Pending on device:** {len(q)}")
    if q: st.dataframe(pd.DataFrame(q)[["village", "incident", "severity", "status", "checksum"]], hide_index=True)
    if st.button("🔄 Sync when link returns", disabled=not online or not q):
        sync(loss, latency); st.rerun()
    if not online and q: st.info("Switch the network back ONLINE in the sidebar, then press Sync.")
    st.subheader("Responder dashboard (delivered reports)")
    dl = st.session_state.delivered
    if dl:
        df = pd.DataFrame(dl); st.dataframe(df.drop(columns=["created", "ack"]), hide_index=True)
        st.map(df.rename(columns={"lat": "lat", "lon": "lon"})[["lat", "lon"]])
    else:
        st.caption("No delivered reports yet.")

with tab4:
    n_del, n_q = len(st.session_state.delivered), len(st.session_state.queue)
    delays = [r["sync_delay_s"] for r in st.session_state.delivered if r["sync_delay_s"] is not None]
    m1, m2, m3 = st.columns(3)
    m1.metric("Delivery rate", f"{100 * n_del / max(1, n_del + n_q):.0f}%")
    m2.metric("Avg sync delay (s)", f"{np.mean(delays):.1f}" if delays else "–")
    m3.metric("Reports pending", n_q)
    st.caption("Prototype metrics: queued-message delivery rate after simulated reconnection, sync delay, "
               "and transparent priority scores. Concept only; must complement official emergency channels.")

"""
TERRA-LINK X — Resilient Connectivity, Terrain Context & Emergency Coordination MVP
Runs seamlessly with zero external dependencies (CLI fallback) or with Streamlit/Folium/Pandas if installed.
"""

import base64
import hashlib
import json
import math
import os
import sqlite3
import sys
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# 1. OPTIONAL LIBRARY DETECTION (Zero-Error Graceful Degradation)
# ---------------------------------------------------------------------------
HAS_PANDAS = False
HAS_STREAMLIT = False
HAS_FOLIUM = False

try:
    import pandas as pd
    HAS_PANDAS = True
except ImportError:
    pd = None

try:
    import streamlit as st
    HAS_STREAMLIT = True
except ImportError:
    st = None

try:
    import folium
    from streamlit_folium import st_folium
    HAS_FOLIUM = True
except ImportError:
    folium = None
    st_folium = None


# ---------------------------------------------------------------------------
# 2. CORE ENGINE: DATABASE, ENCRYPTION, AI SCORING & DTN STORE-AND-FORWARD
# ---------------------------------------------------------------------------
DB_PATH = "terra_link_x_mvp.db"

# Sample regional settlements (inspired by proposal priority regions)
SAMPLE_SETTLEMENTS = [
    {
        "id": "SET-01",
        "name": "Chamoli High-Altitude Outpost",
        "region": "Himalayan Belt (Uttarakhand)",
        "lat": 30.4020,
        "lon": 79.3220,
        "elevation_m": 2150,
        "population": 1850,
        "connectivity": "Weak / Intermittent",
        "health_dist_km": 28.5,
        "hazard_risk": 0.85,  # Landslide / snow
        "coverage_gap": 0.80,
    },
    {
        "id": "SET-02",
        "name": "Ukhrul Remote Hill Cluster",
        "region": "North-East (Manipur Hills)",
        "lat": 25.0968,
        "lon": 94.3617,
        "elevation_m": 1662,
        "population": 3200,
        "connectivity": "Offline / Satellite Only",
        "health_dist_km": 34.0,
        "hazard_risk": 0.75,
        "coverage_gap": 0.90,
    },
    {
        "id": "SET-03",
        "name": "Kendrapara Coastal Panchayat",
        "region": "Coastal Flood-Prone (Odisha)",
        "lat": 20.4983,
        "lon": 86.4220,
        "elevation_m": 12,
        "population": 5400,
        "connectivity": "4G Available (Cyclone Vulnerable)",
        "health_dist_km": 9.5,
        "hazard_risk": 0.92,  # Cyclone / flood
        "coverage_gap": 0.35,
    },
    {
        "id": "SET-04",
        "name": "Barmer Desert Hamlet",
        "region": "Arid / Remote Rural (Rajasthan)",
        "lat": 25.7521,
        "lon": 71.3967,
        "elevation_m": 227,
        "population": 2100,
        "connectivity": "2G / Patchy",
        "health_dist_km": 41.0,
        "hazard_risk": 0.50,  # Heatwave / drought
        "coverage_gap": 0.75,
    },
]


class TerraLinkCore:
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self._init_db()

    def _get_conn(self):
        return sqlite3.connect(self.db_path, check_same_thread=False)

    def _init_db(self):
        with self._get_conn() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS reports (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    report_code TEXT UNIQUE,
                    settlement_name TEXT,
                    lat REAL,
                    lon REAL,
                    incident_type TEXT,
                    severity TEXT,
                    description TEXT,
                    contact_consent TEXT,
                    encrypted_payload TEXT,
                    checksum TEXT,
                    created_at TEXT,
                    synced_at TEXT,
                    status TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS system_state (
                    key TEXT PRIMARY KEY,
                    value TEXT
                )
                """
            )
            conn.execute(
                "INSERT OR IGNORE INTO system_state (key, value) VALUES ('network_online', '0')"
            )

    # --- Network State Simulation ---
    def is_network_online(self) -> bool:
        with self._get_conn() as conn:
            cur = conn.execute(
                "SELECT value FROM system_state WHERE key = 'network_online'"
            )
            row = cur.fetchone()
            return row[0] == "1" if row else False

    def set_network_online(self, online: bool):
        with self._get_conn() as conn:
            conn.execute(
                "UPDATE system_state SET value = ? WHERE key = 'network_online'",
                ("1" if online else "0",),
            )
        if online:
            return self.sync_pending_queue()
        return 0

    # --- Lightweight Payload Protection & Integrity Check ---
    @staticmethod
    def _protect_payload(payload_dict: dict, secret_salt: str = "TERRALINK-LOCAL-KEY") -> tuple[str, str]:
        raw_json = json.dumps(payload_dict, sort_keys=True).encode("utf-8")
        key_bytes = hashlib.sha256(secret_salt.encode("utf-8")).digest()
        xored = bytes(b ^ key_bytes[i % len(key_bytes)] for i, b in enumerate(raw_json))
        encoded = base64.urlsafe_b64encode(xored).decode("ascii")
        checksum = hashlib.sha256(raw_json).hexdigest()[:12]
        return encoded, checksum

    # --- Module 2 & 3: Offline-First Report Submission & DTN Queue ---
    def submit_report(
        self,
        settlement_name: str,
        lat: float,
        lon: float,
        incident_type: str,
        severity: str,
        description: str,
        contact_info: str,
        consent_given: bool,
    ) -> dict:
        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        report_code = f"TLX-{int(datetime.now().timestamp()) % 100000}"

        stored_contact = contact_info.strip() if (consent_given and contact_info.strip()) else "ANONYMOUS / NO-CONSENT"

        payload = {
            "report_code": report_code,
            "settlement": settlement_name,
            "coords": [lat, lon],
            "type": incident_type,
            "severity": severity,
            "description": description,
            "contact": stored_contact,
            "timestamp": now_iso,
        }
        encrypted_blob, checksum = self._protect_payload(payload)

        online = self.is_network_online()
        status = "SYNCED (DELIVERED)" if online else "QUEUED (OFFLINE DEVICE)"
        synced_at = now_iso if online else None

        with self._get_conn() as conn:
            conn.execute(
                """
                INSERT INTO reports (
                    report_code, settlement_name, lat, lon, incident_type, severity,
                    description, contact_consent, encrypted_payload, checksum,
                    created_at, synced_at, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    report_code,
                    settlement_name,
                    lat,
                    lon,
                    incident_type,
                    severity,
                    description,
                    stored_contact,
                    encrypted_blob,
                    checksum,
                    now_iso,
                    synced_at,
                    status,
                ),
            )
        return {"report_code": report_code, "status": status, "checksum": checksum}

    def sync_pending_queue(self) -> int:
        if not self.is_network_online():
            return 0
        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        with self._get_conn() as conn:
            cur = conn.execute(
                """
                UPDATE reports
                SET status = 'SYNCED (DELIVERED)', synced_at = ?
                WHERE status = 'QUEUED (OFFLINE DEVICE)'
                """,
                (now_iso,),
            )
            return cur.rowcount

    def acknowledge_report(self, report_code: str):
        with self._get_conn() as conn:
            conn.execute(
                """
                UPDATE reports
                SET status = 'ACKNOWLEDGED BY RESPONDER'
                WHERE report_code = ? AND status != 'QUEUED (OFFLINE DEVICE)'
                """,
                (report_code,),
            )

    def get_all_reports(self) -> list[dict]:
        with self._get_conn() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("SELECT * FROM reports ORDER BY id DESC").fetchall()
            return [dict(r) for r in rows]

    # --- Module 4: Terrain & Route Context Estimator ---
    @staticmethod
    def estimate_terrain_context(lat1: float, lon1: float, elev1: float, lat2: float = 28.6139, lon2: float = 77.2090) -> dict:
        # Haversine straight-line distance to nearest district hub (default hub coords)
        r = 6371.0
        phi1, phi2 = math.radians(lat1), math.radians(lat2)
        dphi = math.radians(lat2 - lat1)
        dlambda = math.radians(lon2 - lon1)
        a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
        straight_km = 2 * r * math.atan2(math.sqrt(a), math.sqrt(1 - a))

        # Terrain tortuosity factor based on elevation
        terrain_factor = 1.65 if elev1 > 1200 else (1.30 if elev1 > 300 else 1.20)
        est_road_km = round(straight_km * terrain_factor, 1)
        avg_speed_kmh = 22.0 if elev1 > 1200 else 40.0
        est_hours = round(est_road_km / avg_speed_kmh, 1)

        return {
            "elevation_m": elev1,
            "terrain_class": "High-Gradient Mountain" if elev1 > 1200 else ("Undulating / Arid" if elev1 > 200 else "Lowland / Coastal"),
            "est_road_km_to_hub": est_road_km,
            "est_travel_hours": est_hours,
            "freshness_note": "Cached offline DEM/OSM baseline (verify local roadblocks before dispatch)",
        }

    # --- Module 5: Transparent AI-Assisted Connectivity Prioritization ---
    @staticmethod
    def rank_connectivity_priorities(
        settlements: list[dict],
        w_gap: float = 0.35,
        w_hazard: float = 0.30,
        w_health_dist: float = 0.20,
        w_pop: float = 0.15,
    ) -> list[dict]:
        total_w = max(w_gap + w_hazard + w_health_dist + w_pop, 1e-6)
        w_gap, w_hazard, w_health_dist, w_pop = (
            w_gap / total_w,
            w_hazard / total_w,
            w_health_dist / total_w,
            w_pop / total_w,
        )

        ranked = []
        for s in settlements:
            norm_health = min(s["health_dist_km"] / 50.0, 1.0)
            norm_pop = min(s["population"] / 6000.0, 1.0)

            score = (
                w_gap * s["coverage_gap"]
                + w_hazard * s["hazard_risk"]
                + w_health_dist * norm_health
                + w_pop * norm_pop
            ) * 100.0

            ranked.append(
                {
                    "ID": s["id"],
                    "Settlement": s["name"],
                    "Region": s["region"],
                    "Connectivity Status": s["connectivity"],
                    "Population": s["population"],
                    "Health Facility Dist (km)": s["health_dist_km"],
                    "Hazard Exposure": round(s["hazard_risk"], 2),
                    "Coverage Gap": round(s["coverage_gap"], 2),
                    "Priority Score (0-100)": round(score, 1),
                    "Explanation": (
                        f"Gap({int(s['coverage_gap']*100)}%) + "
                        f"Hazard({int(s['hazard_risk']*100)}%) + "
                        f"HealthDist({s['health_dist_km']}km)"
                    ),
                }
            )
        ranked.sort(key=lambda x: x["Priority Score (0-100)"], reverse=True)
        return ranked


# ---------------------------------------------------------------------------
# 3. WEB DASHBOARD MODE (Runs if Streamlit is installed)
# ---------------------------------------------------------------------------
def run_streamlit_app(core: TerraLinkCore):
    st.set_page_config(page_title="TERRA-LINK X Prototype", layout="wide")
    st.title("TERRA-LINK X — Resilient Connectivity & Emergency Coordination")
    st.caption(
        "Student Concept MVP | Offline-First Store-and-Forward Queue, GIS Context & Transparent AI Prioritization"
    )

    # Sidebar: Network Outage Simulator & Metrics
    with st.sidebar:
        st.header("1. Network Link Simulator")
        current_online = core.is_network_online()
        new_online = st.toggle(
            "Communication Link Available (Online)",
            value=current_online,
            help="Toggle OFF to simulate cellular/broadband outage; toggle ON to auto-sync queued reports.",
        )
        if new_online != current_online:
            synced_count = core.set_network_online(new_online)
            if new_online and synced_count > 0:
                st.success(f"Link restored! Auto-synced {synced_count} queued report(s).")
            st.rerun()

        if new_online:
            st.success("STATUS: ONLINE (Live Sync Active)")
        else:
            st.warning("STATUS: OFFLINE (DTN Local Queue Mode)")

        reports = core.get_all_reports()
        queued_cnt = sum(1 for r in reports if "QUEUED" in r["status"])
        synced_cnt = sum(1 for r in reports if "SYNCED" in r["status"])
        ack_cnt = sum(1 for r in reports if "ACKNOWLEDGED" in r["status"])

        st.divider()
        st.subheader("Prototype Telemetry")
        st.metric("Queued Offline Reports", queued_cnt)
        st.metric("Synced to Hub", synced_cnt)
        st.metric("Responder Acknowledged", ack_cnt)

    tab1, tab2, tab3 = st.tabs(
        [
            "Map & Terrain Context",
            "Offline Emergency Report & Queue",
            "AI Connectivity Gap Prioritization",
        ]
    )

    # --- TAB 1: Map & Terrain Context ---
    with tab1:
        st.subheader("Rural Connectivity & Terrain Intelligence")
        if HAS_FOLIUM:
            m = folium.Map(location=[24.5, 81.0], zoom_start=5)
            for s in SAMPLE_SETTLEMENTS:
                color = "red" if s["coverage_gap"] >= 0.75 else ("orange" if s["coverage_gap"] >= 0.5 else "green")
                popup_html = (
                    f"<b>{s['name']}</b><br>"
                    f"Region: {s['region']}<br>"
                    f"Connectivity: {s['connectivity']}<br>"
                    f"Elevation: {s['elevation_m']} m<br>"
                    f"Nearest Clinic: {s['health_dist_km']} km"
                )
                folium.Marker(
                    [s["lat"], s["lon"]],
                    popup=popup_html,
                    tooltip=f"{s['name']} ({s['connectivity']})",
                    icon=folium.Icon(color=color),
                ).add_to(m)
            st_folium(m, width=900, height=400)
        else:
            st.info("Install `folium` and `streamlit-folium` for interactive map tiles. Showing tabular GIS coordinates below.")

        terrain_rows = []
        for s in SAMPLE_SETTLEMENTS:
            ctx = core.estimate_terrain_context(s["lat"], s["lon"], s["elevation_m"])
            terrain_rows.append(
                {
                    "Settlement": s["name"],
                    "Lat/Lon": f"{s['lat']}, {s['lon']}",
                    "Connectivity": s["connectivity"],
                    "Terrain Class": ctx["terrain_class"],
                    "Est. Road Dist to Hub (km)": ctx["est_road_km_to_hub"],
                    "Est. Transit (hrs)": ctx["est_travel_hours"],
                    "Data Caveat": ctx["freshness_note"],
                }
            )
        st.dataframe(pd.DataFrame(terrain_rows) if HAS_PANDAS else terrain_rows, use_container_width=True)

    # --- TAB 2: Emergency Report Form & Responder Dashboard ---
    with tab2:
        col_form, col_dash = st.columns([1, 1.3])
        with col_form:
            st.subheader("Submit Field / Emergency Report")
            with st.form("incident_form", clear_on_submit=True):
                sel_name = st.selectbox("Settlement / Location", [s["name"] for s in SAMPLE_SETTLEMENTS])
                sel_obj = next(s for s in SAMPLE_SETTLEMENTS if s["name"] == sel_name)
                inc_type = st.selectbox(
                    "Incident Category",
                    ["Medical Emergency", "Landslide / Road Block", "Flood / Cyclone Alert", "Telecom / Power Outage"],
                )
                severity = st.select_slider("Severity Level", options=["Low", "Moderate", "High", "Critical"], value="High")
                desc = st.text_area("Situation Summary", placeholder="Describe injuries, blocked routes, or local needs...")
                contact = st.text_input("Optional Contact Number / Radio Callsign")
                consent = st.checkbox("I consent to sharing contact details with authorized district responders", value=True)
                submitted = st.form_submit_button("Save / Transmit Report")

                if submitted:
                    res = core.submit_report(
                        settlement_name=sel_obj["name"],
                        lat=sel_obj["lat"],
                        lon=sel_obj["lon"],
                        incident_type=inc_type,
                        severity=severity,
                        description=desc or "No additional notes provided.",
                        contact_info=contact,
                        consent_given=consent,
                    )
                    st.info(f"Report **{res['report_code']}** recorded | Status: **{res['status']}** | SHA256: `{res['checksum']}`")
                    st.rerun()

        with col_dash:
            st.subheader("Authorized Coordination Dashboard & Queue")
            reports = core.get_all_reports()
            if not reports:
                st.write("No reports recorded yet. Submit a test report on the left (try toggling Offline first).")
            else:
                for r in reports:
                    with st.expander(f"[{r['status']}] {r['report_code']} — {r['incident_type']} ({r['settlement_name']})"):
                        st.write(f"**Severity:** {r['severity']} | **Created:** {r['created_at']}")
                        st.write(f"**Details:** {r['description']}")
                        st.write(f"**Contact:** {r['contact_consent']} | **Integrity Checksum:** `{r['checksum']}`")
                        st.code(f"Encrypted Queue Payload: {r['encrypted_payload'][:64]}...", language="text")
                        if r["status"] == "SYNCED (DELIVERED)":
                            if st.button(f"Acknowledge {r['report_code']}", key=f"ack_{r['report_code']}"):
                                core.acknowledge_report(r["report_code"])
                                st.rerun()

    # --- TAB 3: Transparent AI-Assisted Prioritization ---
    with tab3:
        st.subheader("Transparent Connectivity-Investment Priority Scoring")
        st.write("Adjust policy weights below to inspect how settlements are ranked without black-box opacity:")
        c1, c2, c3, c4 = st.columns(4)
        w1 = c1.slider("Coverage Gap Weight", 0.0, 1.0, 0.35)
        w2 = c2.slider("Hazard Exposure Weight", 0.0, 1.0, 0.30)
        w3 = c3.slider("Health Distance Weight", 0.0, 1.0, 0.20)
        w4 = c4.slider("Population Weight", 0.0, 1.0, 0.15)

        ranked = core.rank_connectivity_priorities(SAMPLE_SETTLEMENTS, w1, w2, w3, w4)
        st.dataframe(pd.DataFrame(ranked) if HAS_PANDAS else ranked, use_container_width=True)


# ---------------------------------------------------------------------------
# 4. ZERO-DEPENDENCY CLI FALLBACK MODE (Runs if Streamlit is NOT installed)
# ---------------------------------------------------------------------------
def run_cli_app(core: TerraLinkCore):
    print("\n" + "=" * 72)
    print("TERRA-LINK X — Resilient Connectivity & Emergency Coordination (CLI Mode)")
    print("Note: Running in zero-dependency standard-library mode (no external packages needed).")
    print("      Install `streamlit`, `pandas`, `folium`, `streamlit-folium` for Web UI.")
    print("=" * 72)

    while True:
        net_status = "ONLINE (Live Sync)" if core.is_network_online() else "OFFLINE (Local Store-and-Forward Queue)"
        reports = core.get_all_reports()
        queued = sum(1 for r in reports if "QUEUED" in r["status"])

        print(f"\n[Network State: {net_status} | Total Reports: {len(reports)} | Pending Queue: {queued}]")
        print("1. Toggle Network Online/Offline (simulates outage & auto-sync)")
        print("2. Submit Emergency / Connectivity Report")
        print("3. View Responder Dashboard & Acknowledge Synced Reports")
        print("4. View AI Connectivity Gap Prioritization & Terrain Context")
        print("5. Exit")

        choice = input("\nSelect an option (1-5): ").strip()

        if choice == "1":
            new_state = not core.is_network_online()
            synced = core.set_network_online(new_state)
            print(f"--> Network switched to {'ONLINE' if new_state else 'OFFLINE'}.")
            if new_state:
                print(f"--> Synchronized {synced} pending offline report(s) to coordination hub.")

        elif choice == "2":
            print("\nAvailable Settlements:")
            for idx, s in enumerate(SAMPLE_SETTLEMENTS, 1):
                print(f"  {idx}. {s['name']} [{s['region']}] - {s['connectivity']}")
            try:
                s_idx = int(input("Choose settlement number (1-4): ").strip()) - 1
                sel = SAMPLE_SETTLEMENTS[s_idx]
            except (ValueError, IndexError):
                sel = SAMPLE_SETTLEMENTS[0]
                print(f"Defaulting to {sel['name']}.")

            inc_type = input("Incident Type (e.g., Medical Emergency, Landslide, Flood): ").strip() or "Medical Emergency"
            severity = input("Severity (Low / Moderate / High / Critical): ").strip() or "High"
            desc = input("Brief Description: ").strip() or "Field report logged."
            contact = input("Contact Info (leave blank for anonymous): ").strip()
            consent = input("Consent to share contact with responders? (y/n): ").strip().lower() == "y"

            res = core.submit_report(
                sel["name"], sel["lat"], sel["lon"], inc_type, severity, desc, contact, consent
            )
            print(f"--> Saved {res['report_code']} | Status: {res['status']} | Integrity Hash: {res['checksum']}")

        elif choice == "3":
            print("\n--- RESPONDER COORDINATION DASHBOARD ---")
            if not reports:
                print("No reports found.")
            else:
                for r in reports:
                    print(
                        f"- {r['report_code']} | {r['status']} | {r['incident_type']} ({r['severity']}) "
                        f"@ {r['settlement_name']} | Contact: {r['contact_consent']}"
                    )
                ack_id = input("\nEnter Report Code to acknowledge (or press Enter to skip): ").strip()
                if ack_id:
                    core.acknowledge_report(ack_id)
                    print(f"--> Updated {ack_id} (only synced reports can be acknowledged).")

        elif choice == "4":
            print("\n--- TRANSPARENT AI CONNECTIVITY PRIORITY & TERRAIN CONTEXT ---")
            ranked = core.rank_connectivity_priorities(SAMPLE_SETTLEMENTS)
            for idx, item in enumerate(ranked, 1):
                s_orig = next(s for s in SAMPLE_SETTLEMENTS if s["id"] == item["ID"])
                terrain = core.estimate_terrain_context(s_orig["lat"], s_orig["lon"], s_orig["elevation_m"])
                print(
                    f"{idx}. {item['Settlement']} | Score: {item['Priority Score (0-100)']}/100 | "
                    f"Drivers: {item['Explanation']} | Terrain: {terrain['terrain_class']} "
                    f"(~{terrain['est_road_km_to_hub']} km / {terrain['est_travel_hours']} hrs to hub)"
                )

        elif choice == "5":
            print("Exiting TERRA-LINK X MVP.")
            break
        else:
            print("Invalid choice. Please enter 1-5.")


# ---------------------------------------------------------------------------
# 5. ENTRY POINT ROUTER
# ---------------------------------------------------------------------------
def is_running_under_streamlit() -> bool:
    if not HAS_STREAMLIT:
        return False
    try:
        from streamlit.runtime.scriptrunner import get_script_run_ctx
        return get_script_run_ctx() is not None
    except Exception:
        return False


if __name__ == "__main__":
    engine = TerraLinkCore()
    if is_running_under_streamlit():
        run_streamlit_app(engine)
    else:
        run_cli_app(engine)
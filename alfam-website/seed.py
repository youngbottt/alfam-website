import sqlite3, datetime as dt, secrets, sys
sys.path.insert(0, "/home/user/alfam_platform")
from app import db, PRODUCTS, indicative_premium, init_db

init_db()
now = dt.datetime.now().isoformat(timespec="seconds")
demo = [
    dict(ref="ALF-260901-DEMO", client_name="Adaeze Okonkwo", client_email="client@example.com",
         client_phone="0803 000 1111", client_company="Okada Foods Ltd", product="business",
         asset="Stock, equipment & fittings — Lekki warehouse", sum_insured=85_000_000,
         adjuster_value=92_000_000, notes="Fire and burglary; 24hr security, no prior claims."),
    dict(ref="ALF-260901-MOT", client_name="Emeka Nwosu", client_email="emeka@example.com",
         client_phone="0805 222 3333", client_company="", product="motor",
         asset="2019 Toyota Hilux — private use, Lagos", sum_insured=18_000_000,
         adjuster_value=17_400_000, notes="Comprehensive; tracker fitted."),
]
insurers = [("Leadway Assurance", "underwriting@leadway.example"),
            ("AXA Mansard", "motor@axamansard.example"),
            ("NEM Insurance", "sme@nem.example"),
            ("Custodian & Allied", "quotes@custodian.example")]
quotes = {
    "Leadway Assurance": dict(premium=620_000, excess=150_000, si=85_000_000, breadth=3, service=3,
                              ex="Wear and tear; theft without forced entry"),
    "AXA Mansard":       dict(premium=705_000, excess=100_000, si=92_000_000, breadth=4, service=4,
                              ex="Stock declaration warranty; survey required"),
    "NEM Insurance":     dict(premium=560_000, excess=350_000, si=80_000_000, breadth=3, service=3,
                              ex="Burglary excluded unless alarm active"),
}
with db() as c:
    for d in demo:
        if c.execute("SELECT 1 FROM requests WHERE ref=?", (d["ref"],)).fetchone():
            continue
        cur = c.execute("""INSERT INTO requests(ref, created, client_name, client_email,
                           client_phone, client_company, product, asset, sum_insured,
                           adjuster_value, notes, status, indicative)
                           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (d["ref"], now, d["client_name"], d["client_email"], d["client_phone"],
                         d["client_company"], d["product"], d["asset"], d["sum_insured"],
                         d["adjuster_value"], d["notes"], "terms received",
                         indicative_premium(d["product"], d["sum_insured"])))
        rid = cur.lastrowid
        for name, email in insurers:
            tok = secrets.token_urlsafe(16)
            cur2 = c.execute("INSERT INTO rfq(request_id, insurer, email, token, sent_at, status)"
                             " VALUES(?,?,?,?,?,?)", (rid, name, email, tok, now,
                             "responded" if name in quotes else "pending"))
            if name in quotes:
                q = quotes[name]
                c.execute("""INSERT INTO quotes(rfq_id, request_id, insurer, premium, excess,
                             sum_insured, exclusions, terms, adjuster_value, service_rating,
                             breadth, received) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                          (cur2.lastrowid, rid, name, q["premium"], q["excess"], q["si"],
                           q["ex"], "Premium payable within 30 days of inception.",
                           d["adjuster_value"], q["service"], q["breadth"], now))
print("seeded")

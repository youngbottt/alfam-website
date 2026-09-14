"""
Alfam Insurance Brokers — Broker Operating Platform (2026)
Client quote -> insurer RFQ emails -> insurer responses -> adjuster-value ranking
-> branded BROKER report to the client.

Run: PYTHONPATH=/home/user/.pymods python3 app.py
"""
import os, io, sqlite3, smtplib, secrets, datetime as dt
from email.message import EmailMessage
from email.utils import formataddr
from urllib.parse import urljoin

from flask import (Flask, render_template, request, redirect, url_for, send_file,
                   abort, flash, jsonify)
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
                                Image as RLImage, HRFlowable)

BASE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(BASE, "alfam.db")
OUTBOX = os.path.join(BASE, "outbox")
STATIC = os.path.join(BASE, "static")
os.makedirs(OUTBOX, exist_ok=True)

app = Flask(__name__)
app.secret_key = os.environ.get("ALFAM_SECRET", "alfam-demo-key")

BRAND = dict(
    name="Alfam Insurance Brokers Limited",
    short="Alfam",
    tagline="Protecting what matters. Securing your future.",
    founded=1977,
    phone="0708 306 0861",
    email="alfaminsbrokers@yahoo.com",
    web="alfaminsurancebrokers.com.ng",
    hq="29 Berkley Street, Lagos",
    offices=[("Lagos — Head Office", "29 Berkley Street, Lagos"),
             ("Lekki Phase 1", "15 Olushola Agbaje Street, Lekki Phase 1, Lagos"),
             ("Abuja", "12 Paraku Crescent, Wuse II, Abuja")],
    regulators="Member, Nigerian Council of Registered Insurance Brokers (NCRIB) · Regulated by NAICOM",
)
GREEN = colors.HexColor("#0b5d41")
GOLD = colors.HexColor("#caa33f")
CREAM = colors.HexColor("#fff7d7")
PUBLIC_BASE = os.environ.get("ALFAM_PUBLIC_BASE", "http://localhost:8000")
ADSENSE = os.environ.get("ALFAM_ADSENSE_CLIENT", "")   # e.g. ca-pub-1234567890123456

@app.context_processor
def inject_globals():
    year = dt.date.today().year
    return dict(brand=BRAND, products=PRODUCTS, motor_tp=MOTOR_TP,
                adsense=ADSENSE, now=dt.datetime.now(),
                anniversary=BRAND["founded"] + 50,      # 2027
                years_in_business=year - BRAND["founded"],
                insurer_count=len(INSURERS))

# ------------------------------------------------------------------ rating engine
# Sources: NAICOM-approved third-party motor tariff (private ₦15,000 / TPPD ₦3m;
# commercial ₦20,000 / ₦5m; truck & general cartage ₦100,000 / ₦5m; special types
# ₦20,000 / ₦3m; tricycle ₦5,000 / ₦2m; motorcycle ₦3,000 / ₦1m) and the NAICOM
# directive that comprehensive motor premium may not be below 5% of the sum insured
# after rebates or discounts. Other classes are market-indicative bands.
COMPREHENSIVE_FLOOR = 0.05

MOTOR_TP = {
    "private":    dict(label="Private car", premium=15000, tppd=3_000_000),
    "commercial": dict(label="Commercial / goods & staff bus", premium=20000, tppd=5_000_000),
    "truck":      dict(label="Truck / general cartage", premium=100000, tppd=5_000_000),
    "special":    dict(label="Special types", premium=20000, tppd=3_000_000),
    "tricycle":   dict(label="Tricycle", premium=5000, tppd=2_000_000),
    "motorcycle": dict(label="Motorcycle", premium=3000, tppd=1_000_000),
}

PRODUCTS = {
    "motor": dict(label="Motor", kind="motor", min_prem=15000,
                  note="Third-party follows the NAICOM-approved tariff. Comprehensive may not be "
                       "priced below 5% of the sum insured after rebates or discounts; most "
                       "underwriters rate 5%–7%."),
    "home": dict(label="Home / Householder", kind="rate", rate=0.010, rate_max=0.015,
                 min_prem=45000,
                 note="Buildings, contents and all-risk section; commonly 1.0%–1.5% of rebuilding "
                      "cost plus contents value."),
    "fire": dict(label="Fire & Special Perils (property)", kind="rate", rate=0.0025,
                 rate_max=0.0075, min_prem=100000,
                 note="0.25%–0.75% of rebuilding value. Industrial and high-hazard occupancies "
                      "rate above the band."),
    "burglary": dict(label="Burglary / Housebreaking", kind="rate", rate=0.005, rate_max=0.010,
                     min_prem=60000,
                     note="0.5%–1.0% of stock or contents value, subject to protection warranties."),
    "business": dict(label="Business / SME Package", kind="rate", rate=0.006, rate_max=0.012,
                     min_prem=120000,
                     note="Fire, burglary, all-risk, money, fidelity and business interruption "
                          "combined: 0.6%–1.2% of total sum insured."),
    "git": dict(label="Goods in Transit / Marine Cargo", kind="rate", rate=0.005,
                rate_max=0.0125, min_prem=75000,
                note="Marine cargo is commonly rated around 0.5% of CIF plus 10%; inland transit "
                     "0.5%–1.25% depending on conveyance and per-carrying limits."),
    "marine_hull": dict(label="Marine Hull & Craft", kind="rate", rate=0.015, rate_max=0.025,
                        min_prem=500000,
                        note="1.5%–2.5% of hull value by craft type, trading warranty and record."),
    "engineering": dict(label="Engineering (CAR / EAR / Machinery)", kind="rate", rate=0.005,
                        rate_max=0.015, min_prem=150000,
                        note="Contractors' all risk, erection all risk and machinery breakdown: "
                             "0.5%–1.5% of contract or plant value."),
    "oil_gas": dict(label="Energy / Oil & Gas", kind="rate", rate=0.0075, rate_max=0.020,
                    min_prem=1000000,
                    note="Offshore and onshore operating packages, control of well and operators' "
                         "extra expense; rated per schedule and placed by slip."),
    "life": dict(label="Group Life / Term Assurance", kind="rate", rate=0.005, rate_max=0.010,
                 min_prem=60000,
                 note="Group life commonly 0.5%–1.0% of total sum assured, age-banded. Individual "
                      "term assurance is rated on age, health and term."),
    "travel": dict(label="Travel", kind="rate", rate=0.020, rate_max=0.040, min_prem=20000,
                   note="2%–4% of trip value, or flat per-day and Schengen-rated plans."),
    "pi": dict(label="Professional Indemnity", kind="rate", rate=0.0075, rate_max=0.020,
               min_prem=250000,
               note="0.75%–2.0% of the limit of indemnity, by profession and claims history."),
    "bond": dict(label="Bonds & Guarantees", kind="rate", rate=0.010, rate_max=0.030,
                 min_prem=150000,
                 note="Bid, performance and advance payment bonds: 1%–3% per annum of the bond "
                      "amount, subject to the obligor's credit."),
}

# Non-placement lines shown on the services page
SERVICE_LINES = [
    ("Health / HMO plans", "₦20,000 – ₦60,000 per life per year for standard plans, "
                           "depending on the plan and provider network."),
    ("Group personal accident", "Rated per ₦1m of cover, typically 0.2%–0.5% of the benefit."),
    ("Agriculture / NAIC schemes", "Area-yield and weather-index covers, often subsidised."),
    ("Aviation & marine liability", "Placed by slip through specialist and London markets."),
    ("Takaful / microinsurance", "Available for retail and informal-sector groups."),
]

def indicative_premium(product, value, motor_cover="comprehensive", motor_class="private"):
    """Market-indicative figure only — final terms come from the underwriter."""
    p = PRODUCTS.get(product, PRODUCTS["business"])
    if product == "motor":
        if motor_cover == "tp":
            return float(MOTOR_TP.get(motor_class, MOTOR_TP["private"])["premium"])
        return round(max(value * COMPREHENSIVE_FLOOR, p["min_prem"]), 2)
    return round(max(value * p["rate"], p["min_prem"]), 2)

def premium_band(product, value, motor_cover="comprehensive", motor_class="private"):
    p = PRODUCTS.get(product, PRODUCTS["business"])
    if product == "motor":
        if motor_cover == "tp":
            v = MOTOR_TP.get(motor_class, MOTOR_TP["private"])
            return v["premium"], v["premium"]
        return (round(max(value * COMPREHENSIVE_FLOOR, p["min_prem"]), 2),
                round(value * 0.07, 2))
    return (round(max(value * p["rate"], p["min_prem"]), 2),
            round(max(value * p["rate_max"], p["min_prem"]), 2))

# ------------------------------------------------------------------ insurer market
# 43 insurers confirmed by NAICOM as compliant with the NIIRA 2025 recapitalisation
# minimum (23 non-life, 10 life, 8 composite, 2 reinsurance).
INSURERS = [
    ("Zenith General Insurance Company Limited", "Non-Life"),
    ("Custodian and Allied Insurance Limited", "Non-Life"),
    ("NEM Insurance Plc", "Non-Life"),
    ("Heirs General Insurance Limited", "Non-Life"),
    ("Fin Insurance Company Limited", "Non-Life"),
    ("Tangerine General Insurance Ltd", "Non-Life"),
    ("Capital Express Indemnity Insurance Limited", "Non-Life"),
    ("Sanlam-Allianz General Insurance Nigeria Ltd", "Non-Life"),
    ("Consolidated Hallmark Insurance Limited", "Non-Life"),
    ("Sterling Assurance Nigeria Limited", "Non-Life"),
    ("Unitrust Insurance Co. Limited", "Non-Life"),
    ("NSIA Insurance Limited", "Non-Life"),
    ("Rex Insurance Limited", "Non-Life"),
    ("Linkage Assurance Plc", "Non-Life"),
    ("Anchor Insurance Company Ltd", "Non-Life"),
    ("Sunu Assurances Nigeria Plc", "Non-Life"),
    ("KBL Insurance Ltd", "Non-Life"),
    ("International Energy Insurance Plc", "Non-Life"),
    ("Veritas Kapital Assurance Plc", "Non-Life"),
    ("NPF Insurance Company Ltd", "Non-Life"),
    ("Coronation Insurance Plc", "Non-Life"),
    ("Prestige Assurance Plc", "Non-Life"),
    ("Mutual Benefits Assurance Plc", "Non-Life"),
    ("Custodian Life Assurance Limited", "Life"),
    ("CHI Life Assurance Limited", "Life"),
    ("Heirs Life Assurance Limited", "Life"),
    ("Prudential Zenith Life Insurance Ltd", "Life"),
    ("Stanbic IBTC Insurance Limited", "Life"),
    ("Sanlam-Allianz Life Insurance Nigeria Limited", "Life"),
    ("Capital Express Life Assurance Limited", "Life"),
    ("Mutual Benefits Life Assurance Ltd", "Life"),
    ("Enterprise Life Assurance Company (Nigeria) Ltd", "Life"),
    ("Coronation Life Assurance Limited", "Life"),
    ("Leadway Assurance Company Limited", "Composite"),
    ("AIICO Insurance Plc", "Composite"),
    ("Cornerstone Insurance Plc", "Composite"),
    ("AXA Mansard Insurance Plc", "Composite"),
    ("LASACO Assurance Plc", "Composite"),
    ("Fortis Global Insurance Plc", "Composite"),
    ("Industrial and General Insurance Plc", "Composite"),
    ("Great Nigeria Insurance Plc", "Composite"),
    ("Continental Reinsurance Plc", "Reinsurance"),
    ("FBS Reinsurance Limited", "Reinsurance"),
]

def insurer_email(name):
    slug = (name.lower().replace(".", "").replace(",", "")
            .replace("(", "").replace(")", "")
            .split()[0])
    return f"underwriting@{slug}.example"

# ------------------------------------------------------------------ db
def db():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c

def migrate():
    """add columns introduced after the first release"""
    with db() as c:
        cols = {r["name"] for r in c.execute("PRAGMA table_info(requests)")}
        for col, ddl in (("motor_cover", "TEXT"), ("motor_class", "TEXT")):
            if col not in cols:
                c.execute(f"ALTER TABLE requests ADD COLUMN {col} {ddl}")
migrate()

def init_db():
    with db() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS requests(
          id INTEGER PRIMARY KEY AUTOINCREMENT, ref TEXT UNIQUE, created TEXT,
          client_name TEXT, client_email TEXT, client_phone TEXT, client_company TEXT,
          product TEXT, asset TEXT, sum_insured REAL, adjuster_value REAL,
          motor_cover TEXT, motor_class TEXT,
          notes TEXT, status TEXT DEFAULT 'new', indicative REAL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS rfq(
          id INTEGER PRIMARY KEY AUTOINCREMENT, request_id INTEGER, insurer TEXT,
          email TEXT, token TEXT UNIQUE, sent_at TEXT, status TEXT DEFAULT 'pending');
        CREATE TABLE IF NOT EXISTS quotes(
          id INTEGER PRIMARY KEY AUTOINCREMENT, rfq_id INTEGER, request_id INTEGER,
          insurer TEXT, premium REAL, excess REAL, sum_insured REAL, exclusions TEXT,
          terms TEXT, adjuster_value REAL, service_rating INTEGER DEFAULT 3,
          breadth INTEGER DEFAULT 3, score REAL DEFAULT 0, received TEXT);
        CREATE TABLE IF NOT EXISTS insurers(id INTEGER PRIMARY KEY AUTOINCREMENT,
          name TEXT UNIQUE, category TEXT);
        CREATE TABLE IF NOT EXISTS clients(id INTEGER PRIMARY KEY AUTOINCREMENT,
          name TEXT, sector TEXT, since TEXT, note TEXT);
        CREATE TABLE IF NOT EXISTS enquiries(id INTEGER PRIMARY KEY AUTOINCREMENT,
          created TEXT, name TEXT, email TEXT, phone TEXT, subject TEXT, message TEXT);
        """)
        for n, cat in INSURERS:
            c.execute("INSERT OR IGNORE INTO insurers(name, category) VALUES(?,?)", (n, cat))
init_db()

# Real client register, taken from the official Alfam website
# ("clients we work for and have worked with since we started business").
# No start years are shown because the register does not publish them.
REAL_CLIENTS = [
    # Government parastatals
    ("Lagos State Government", "Government Parastatals", "", ""),
    ("Nigerian Ports Authority (NPA)", "Government Parastatals", "", ""),
    ("Nigerian Security Printing & Minting Co. Ltd.", "Government Parastatals", "", ""),
    ("Office of the Head of Service of the Federation", "Government Parastatals", "", ""),
    ("Federal Capital Territory Administration, Abuja (FCTA)", "Government Parastatals", "", ""),
    ("Asset Management Corporation of Nigeria (AMCON)", "Government Parastatals", "", ""),
    ("Federal Aviation Authority of Nigeria (FAAN)", "Government Parastatals", "", ""),
    # Oil & gas
    ("Crestech Engineering Limited", "Oil & Gas", "", ""),
    ("Cotsgas International Energy Ltd.", "Oil & Gas", "", ""),
    # Financial
    ("Bank of Agriculture", "Financial Sector", "", ""),
    ("CDL Asset Management Ltd.", "Financial Sector", "", ""),
    # Transportation
    ("Nikky Taurus Nig. Ltd", "Transportation", "", ""),
    # Manufacturing
    ("Onward Paper Mill Ltd.", "Manufacturing", "", ""),
    ("Rajrab Limited / Tempo Foods & Packaging Ltd.", "Manufacturing", "", ""),
    # Corporate
    ("ENL Consortium Ltd.", "Corporate Clients", "", ""),
    ("Peir Points Limited", "Corporate Clients", "", ""),
    ("Akinsehinde Lakanu & Co", "Corporate Clients", "", ""),
    ("O. M. Akintola & Co (Legal Practitioners)", "Corporate Clients", "", ""),
    ("Aksaw Development Ltd.", "Corporate Clients", "", ""),
    ("Hoganguards Nig. Ltd.", "Corporate Clients", "", ""),
    ("Lobila Stores", "Corporate Clients", "", ""),
    ("Dallad Limited", "Corporate Clients", "", ""),
    ("Kunle Oshinaike & Co.", "Corporate Clients", "", ""),
    ("Cotsgas Nig. Ltd.", "Corporate Clients", "", ""),
    ("Onward Stationeries Stores Ltd.", "Corporate Clients", "", ""),
    ("Kofo Coker & Co.", "Corporate Clients", "", ""),
    # Engineering & construction
    ("FMA Architects Ltd", "Engineering / Construction", "", ""),
    ("Delano Architects", "Engineering / Construction", "", ""),
    ("Quess Partnership", "Engineering / Construction", "", ""),
    ("Ladiom Associates", "Engineering / Construction", "", ""),
    ("Pinconsult Associates", "Engineering / Construction", "", ""),
    ("Suctone Ventures", "Engineering / Construction", "", ""),
    ("Plycon Construction Co. Ltd.", "Engineering / Construction", "", ""),
    # Education
    ("Lekki British International High & Junior Schools", "Educational Sector", "", ""),
    ("Hossanah International School", "Educational Sector", "", ""),
    # Individuals & estates
    ("Chief & Mrs. Anthony Ani", "Individual Businesses", "", ""),
    ("Chief Femi Majekodunmi", "Individual Businesses", "", ""),
    ("Mr. A. I. Ibirogba", "Individual Businesses", "", ""),
    ("Prof. Oladipo O. Hunponu-Wusu", "Individual Businesses", "", ""),
    ("Prof. Theo Ogunbiyi", "Individual Businesses", "", ""),
    ("Arc. O. Delano", "Individual Businesses", "", ""),
    ("Mr. Raymond Kotey", "Individual Businesses", "", ""),
    ("Mr. J. O. Macgregor", "Individual Businesses", "", ""),
    ("Chief (Dr.) Sonny Kuku", "Individual Businesses", "", ""),
    ("Chief Mrs. M. O. Orija", "Individual Businesses", "", ""),
    ("Mr. E. Dixon", "Individual Businesses", "", ""),
    ("Mr. & Mrs. Kofo Coker", "Individual Businesses", "", ""),
    ("Mr. Awadagin Thomas", "Individual Businesses", "", ""),
    ("Estate of Mr. & Mrs. J. S. Macgregor", "Individual Businesses", "", ""),
    ("Estate of Mr. Adedeji A. Odunuga", "Individual Businesses", "", ""),
    # Diplomatic
    ("Cameroon Embassy", "Embassy / High Commission", "", ""),
    # Bankers (listed separately — not clients)
    ("First Bank of Nigeria Plc — Moloney branch, Lagos", "Bankers", "", ""),
    ("Guaranty Trust Bank Plc — Adeyemo Alakija branch, Victoria Island, Lagos", "Bankers", "", ""),
    ("Skye Bank Plc — City Hall branch, Igbosere Road, Lagos (now Polaris Bank)", "Bankers", "", ""),
    ("Access Bank Plc — Moloney branch, Lagos", "Bankers", "", ""),
]

def seed_clients():
    with db() as c:
        c.execute("DELETE FROM clients WHERE name LIKE 'Sample%'")
        if c.execute("SELECT COUNT(*) n FROM clients").fetchone()["n"] == 0:
            c.executemany("INSERT INTO clients(name, sector, since, note) VALUES(?,?,?,?)",
                          REAL_CLIENTS)
seed_clients()

# ------------------------------------------------------------------ scoring
def score_quotes(quotes, adjuster_value):
    if not quotes:
        return []
    lo_p = min((q["premium"] or 1) for q in quotes) or 1
    lo_e = min((q["excess"] or 1) for q in quotes) or 1
    for q in quotes:
        s = 40 * (lo_p / (q["premium"] or lo_p))          # price
        s += 15 * (lo_e / (q["excess"] or lo_e))          # excess
        s += 3 * (q["breadth"] or 3)                      # breadth of cover
        s += 2 * (q["service_rating"] or 3)               # claims record
        av = q["adjuster_value"] or adjuster_value or 0
        si = q["sum_insured"] or 0
        s += 20 if (av and si and si / av >= 1) else (20 * max(si / av, 0) if av else 10)
        q["adequacy"] = round((si / av) if av else 0, 3)
        q["score"] = round(s, 1)
    return sorted(quotes, key=lambda q: -q["score"])

# ------------------------------------------------------------------ mail
def smtp_conf():
    return dict(host=os.environ.get("ALFAM_SMTP_HOST"),
                port=int(os.environ.get("ALFAM_SMTP_PORT", 587)),
                user=os.environ.get("ALFAM_SMTP_USER"),
                pwd=os.environ.get("ALFAM_SMTP_PASS"),
                from_addr=os.environ.get("ALFAM_FROM", BRAND["email"]))

def send_or_queue(msg, tag):
    cfg = smtp_conf()
    if cfg["host"] and cfg["user"]:
        try:
            with smtplib.SMTP(cfg["host"], cfg["port"]) as s:
                s.starttls(); s.login(cfg["user"], cfg["pwd"]); s.send_message(msg)
            return "sent"
        except Exception as e:
            print("smtp failed:", e); return "failed"
    with open(os.path.join(OUTBOX, f"{tag}.eml"), "wb") as f:
        f.write(msg.as_bytes())
    return "queued"

def rfq_email(req, insurer, email, token):
    link = urljoin(PUBLIC_BASE, f"/insurer/{token}")
    due = (dt.date.today() + dt.timedelta(days=3)).strftime("%A %d %B %Y")
    prod = PRODUCTS.get(req["product"], {}).get("label", req["product"])
    extra = ""
    if req["product"] == "motor":
        mc = MOTOR_TP.get(req["motor_class"] or "private", MOTOR_TP["private"])
        extra = (f"Cover required: {'Third-party only' if req['motor_cover']=='tp' else 'Comprehensive'}\n"
                 f"Vehicle class: {mc['label']}\n")
    body = f"""Dear Underwriting Team ({insurer}),

{BRAND['name']} requests your quotation for the risk below.

Reference:      {req['ref']}
Client:         {req['client_name']}{' (' + req['client_company'] + ')' if req['client_company'] else ''}
Class:          {prod}
Interest:       {req['asset']}
Sum insured:    NGN {req['sum_insured']:,.0f}
Adjuster value: NGN {(req['adjuster_value'] or 0):,.0f}
{extra}Notes:         {req['notes'] or '-'}

Submit your terms here (no login required):
{link}

Kindly state premium, excess/deductible, exclusions, subjectivities, warranties and
your own assessed value. Response requested by {due}.

All terms are presented to the client in a single consolidated report issued by
{BRAND['name']} as the broker.

Regards,
Broker Placement Desk
{BRAND['name']} | {BRAND['phone']} | {BRAND['email']} | {BRAND['web']}
"""
    m = EmailMessage()
    m["Subject"] = f"Request for quotation - {req['ref']} - {prod}"
    m["From"] = formataddr((BRAND["name"], smtp_conf()["from_addr"]))
    m["To"] = email
    m.set_content(body)
    return m

def report_email(req, pdf_path):
    body = f"""Dear {req['client_name'].split()[0]},

Thank you for giving {BRAND['name']} the opportunity to place your
{PRODUCTS.get(req['product'], {}).get('label', 'insurance')} cover.

We approached insurers on your behalf, compared the terms received and prepared the
attached report for you. It is our independent broker recommendation - not an
insurer's marketing document - and it sets out:

  * the options received, ranked on value rather than price alone
  * what each quotation covers, and the exclusions to watch
  * how each sum insured compares with the adjuster's assessed value
  * our recommendation and the next step

Please review the attached report and tell us which option you would like us to
place. We will handle issuance, endorsements and any future claim.

Yours sincerely,
Broker Placement Desk
{BRAND['name']}
{BRAND['phone']} | {BRAND['email']} | {BRAND['web']}
{BRAND['regulators']}
"""
    m = EmailMessage()
    m["Subject"] = f"Your insurance options - {req['ref']} - {BRAND['name']}"
    m["From"] = formataddr((BRAND["name"], smtp_conf()["from_addr"]))
    m["To"] = req["client_email"]
    m.set_content(body)
    with open(pdf_path, "rb") as f:
        m.add_attachment(f.read(), maintype="application", subtype="pdf",
                         filename=f"Alfam-Broker-Report-{req['ref']}.pdf")
    return m

# ------------------------------------------------------------------ pdf
def build_report(req, quotes):
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=18*mm, rightMargin=18*mm,
                            topMargin=16*mm, bottomMargin=16*mm,
                            title=f"Broker Report {req['ref']}", author=BRAND["name"])
    ss = getSampleStyleSheet()
    H1 = ParagraphStyle("H1", parent=ss["Title"], textColor=GREEN, fontSize=19, spaceAfter=2)
    SUB = ParagraphStyle("SUB", parent=ss["Normal"], fontSize=9, textColor=colors.HexColor("#5c6a60"))
    H2 = ParagraphStyle("H2", parent=ss["Heading2"], textColor=GREEN, fontSize=12, spaceBefore=10)
    P = ParagraphStyle("P", parent=ss["Normal"], fontSize=9, leading=13)
    small = ParagraphStyle("small", parent=ss["Normal"], fontSize=7.5, leading=10,
                           textColor=colors.HexColor("#5c6a60"))
    story = []
    logo = os.path.join(STATIC, "img", "emblem.png")
    cell_logo = RLImage(logo, width=24*mm, height=24*mm) if os.path.exists(logo) else ""
    t = Table([[cell_logo, Paragraph(
        f"{BRAND['name']}<br/><font size=8 color='#caa33f'>{BRAND['tagline']}</font><br/>"
        f"<font size=7>Since {BRAND['founded']} · {BRAND['regulators']}</font>", H1)]],
        colWidths=[28*mm, 146*mm])
    t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                           ("LEFTPADDING", (0, 0), (-1, -1), 0)]))
    story += [t, HRFlowable(width="100%", color=GOLD, thickness=1.2, spaceBefore=4, spaceAfter=8)]
    story.append(Paragraph("Independent Broking Report &amp; Recommendation", H1))
    story.append(Paragraph(f"Reference {req['ref']} | Prepared {dt.date.today():%d %B %Y} | "
                           f"{PRODUCTS.get(req['product'], {}).get('label', req['product'])}", SUB))
    story.append(Spacer(1, 8))

    rows = [["Client", f"{req['client_name']}" + (f" ({req['client_company']})" if req['client_company'] else "")],
            ["Contact", f"{req['client_phone']} | {req['client_email']}"],
            ["Interest insured", req["asset"] or "-"],
            ["Sum insured requested", f"NGN {req['sum_insured']:,.0f}"],
            ["Adjuster's assessed value", f"NGN {req['adjuster_value'] or 0:,.0f}"],
            ["Indicative market premium", f"NGN {req['indicative']:,.0f}"]]
    if req["product"] == "motor":
        mc = MOTOR_TP.get(req["motor_class"] or "private", MOTOR_TP["private"])
        rows.append(["Motor", f"{'Third-party only' if req['motor_cover']=='tp' else 'Comprehensive'} "
                              f"· {mc['label']} · TPPD NGN {mc['tppd']:,.0f}"])
    tb = Table([[Paragraph(f"<b>{a}</b>", small), Paragraph(str(b), P)] for a, b in rows],
               colWidths=[52*mm, 122*mm])
    tb.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#cfe0d5")),
                            ("BACKGROUND", (0, 0), (0, -1), CREAM),
                            ("VALIGN", (0, 0), (-1, -1), "TOP"),
                            ("TOPPADDING", (0, 0), (-1, -1), 4),
                            ("BOTTOMPADDING", (0, 0), (-1, -1), 4)]))
    story += [Paragraph("1. Your requirement", H2), tb]

    if quotes:
        data = [[Paragraph(f"<b>{h}</b>", small) for h in
                 ["Insurer", "Premium (NGN)", "Excess", "Sum insured", "vs adjuster", "Score"]]]
        for q in quotes:
            data.append([Paragraph(q["insurer"], P),
                         Paragraph(f"{q['premium']:,.0f}", P),
                         Paragraph(f"{(q['excess'] or 0):,.0f}", P),
                         Paragraph(f"{(q['sum_insured'] or 0):,.0f}", P),
                         Paragraph(f"{q['adequacy']*100:,.0f}%", P),
                         Paragraph(f"<b>{q['score']:.1f}</b>", P)])
        ct = Table(data, colWidths=[46*mm, 30*mm, 24*mm, 30*mm, 24*mm, 20*mm])
        style = [("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#cfe0d5")),
                 ("BACKGROUND", (0, 0), (-1, 0), GREEN),
                 ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                 ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                 ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4)]
        style.append(("BACKGROUND", (0, 1), (-1, 1), CREAM))
        ct.setStyle(TableStyle(style))
        story += [Paragraph("2. Terms received, ranked by value", H2), ct,
                  Paragraph("Scores blend premium (40), excess (15), breadth of cover (15), "
                            "claims service record (10) and adequacy against the adjuster's "
                            "assessed value (20). Top row = our recommendation.", small)]
        best = quotes[0]
        story += [Paragraph("3. Our recommendation", H2),
                  Paragraph(f"We recommend <b>{best['insurer']}</b> at a premium of "
                            f"<b>NGN {best['premium']:,.0f}</b> with an excess of "
                            f"NGN {best['excess'] or 0:,.0f}. This option scores "
                            f"{best['score']:.1f}/100 and represents "
                            f"{best['adequacy']*100:,.0f}% of the adjuster's assessed value."
                            + (f" Exclusions/subjectivities: {best['exclusions']}."
                               if best["exclusions"] else ""), P)]
        if req["notes"]:
            story.append(Paragraph(f"Broker note: {req['notes']}", small))
    else:
        story += [Paragraph("2. Terms received", H2),
                  Paragraph("Quotation requests have been issued. This report is re-issued with a "
                            "ranked comparison as soon as terms are received.", P)]

    story += [Paragraph("4. Basis of this report", H2),
              Paragraph(f"This report is issued by {BRAND['name']} in our capacity as your broker. "
                        "We act for you, not the insurer. Cover is bound only once the insurer "
                        "confirms acceptance and premium is paid. Indicative figures follow the "
                        "NAICOM-approved third-party motor tariff where applicable and market "
                        "bands elsewhere; all terms remain subject to the policy wording and to "
                        "the underwriter's confirmation.", small)]
    story.append(Spacer(1, 10))
    story.append(HRFlowable(width="100%", color=GOLD, thickness=0.8))
    story.append(Paragraph(f"{BRAND['name']} | {BRAND['hq']} | {BRAND['phone']} | "
                           f"{BRAND['email']} | {BRAND['web']}", small))
    doc.build(story)
    buf.seek(0)
    return buf

# ------------------------------------------------------------------ public pages
@app.route("/")
def index():
    with db() as c:
        stats = dict(requests=c.execute("SELECT COUNT(*) n FROM requests").fetchone()["n"],
                     quotes=c.execute("SELECT COUNT(*) n FROM quotes").fetchone()["n"],
                     clients=c.execute("SELECT COUNT(*) n FROM clients").fetchone()["n"])
    return render_template("index.html", stats=stats, service_lines=SERVICE_LINES)

@app.route("/about")
def about():
    return render_template("about.html")

@app.route("/services")
def services():
    return render_template("services.html", service_lines=SERVICE_LINES)

@app.route("/clients")
def clients():
    with db() as c:
        rows = c.execute("SELECT * FROM clients ORDER BY since").fetchall()
    sectors = {}
    for r in rows:
        sectors.setdefault(r["sector"], []).append(r)
    return render_template("clients.html", sectors=sectors)

@app.route("/claims", methods=["GET", "POST"])
def claims():
    if request.method == "POST":
        f = request.form
        with db() as c:
            c.execute("INSERT INTO enquiries(created, name, email, phone, subject, message)"
                      " VALUES(?,?,?,?,?,?)",
                      (dt.datetime.now().isoformat(timespec="seconds"), f.get("name"),
                       f.get("email"), f.get("phone"), "Claim notification",
                       f"Policy/insurer: {f.get('policy')}\nDate of loss: {f.get('date')}\n"
                       f"Type: {f.get('type')}\n\n{f.get('message')}"))
        flash("Claim notification received. A broker will contact you within one business hour.")
        return redirect(url_for("claims"))
    return render_template("claims.html")

@app.route("/contact", methods=["GET", "POST"])
def contact():
    if request.method == "POST":
        f = request.form
        with db() as c:
            c.execute("INSERT INTO enquiries(created, name, email, phone, subject, message)"
                      " VALUES(?,?,?,?,?,?)",
                      (dt.datetime.now().isoformat(timespec="seconds"), f.get("name"),
                       f.get("email"), f.get("phone"), f.get("subject"), f.get("message")))
        flash("Message received — thank you. A broker will reply shortly.")
        return redirect(url_for("contact"))
    return render_template("contact.html")

@app.route("/privacy")
def privacy():
    return render_template("privacy.html")

# ------------------------------------------------------------------ quote flow
@app.route("/quote", methods=["GET", "POST"])
def quote():
    if request.method == "POST":
        f = request.form
        ref = "ALF-" + dt.datetime.now().strftime("%y%m%d") + "-" + secrets.token_hex(2).upper()
        si = float(f.get("sum_insured") or 0)
        av = float(f.get("adjuster_value") or 0) or None
        mc, mk = f.get("motor_cover", "comprehensive"), f.get("motor_class", "private")
        with db() as c:
            c.execute("""INSERT INTO requests(ref, created, client_name, client_email,
                         client_phone, client_company, product, asset, sum_insured,
                         adjuster_value, motor_cover, motor_class, notes, indicative)
                         VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                      (ref, dt.datetime.now().isoformat(timespec="seconds"),
                       f.get("client_name"), f.get("client_email"), f.get("client_phone"),
                       f.get("client_company"), f.get("product"), f.get("asset"), si, av,
                       mc, mk, f.get("notes"),
                       indicative_premium(f.get("product"), si, mc, mk)))
        return redirect(url_for("thanks", ref=ref))
    return render_template("quote.html")

@app.route("/quote/thanks/<ref>")
def thanks(ref):
    with db() as c:
        r = c.execute("SELECT * FROM requests WHERE ref=?", (ref,)).fetchone()
    if not r:
        abort(404)
    return render_template("thanks.html", r=r)

# ------------------------------------------------------------------ broker desk
@app.route("/admin")
def admin():
    with db() as c:
        rows = c.execute("""SELECT r.*, (SELECT COUNT(*) FROM rfq WHERE request_id=r.id) sent,
                            (SELECT COUNT(*) FROM quotes WHERE request_id=r.id) got
                            FROM requests r ORDER BY r.id DESC""").fetchall()
        enq = c.execute("SELECT * FROM enquiries ORDER BY id DESC LIMIT 5").fetchall()
    return render_template("admin.html", rows=rows, enquiries=enq)

@app.route("/admin/r/<int:rid>")
def detail(rid):
    with db() as c:
        r = c.execute("SELECT * FROM requests WHERE id=?", (rid,)).fetchone()
        rfqs = c.execute("SELECT * FROM rfq WHERE request_id=? ORDER BY id", (rid,)).fetchall()
        qs = c.execute("SELECT * FROM quotes WHERE request_id=?", (rid,)).fetchall()
        market = c.execute("SELECT * FROM insurers ORDER BY category, name").fetchall()
    if not r:
        abort(404)
    quotes = score_quotes([dict(q) for q in qs], r["adjuster_value"])
    lo, hi = premium_band(r["product"], r["sum_insured"], r["motor_cover"], r["motor_class"])
    return render_template("detail.html", r=r, rfqs=rfqs, quotes=quotes, market=market,
                           band=(lo, hi),
                           mail_mode=("live" if smtp_conf()["host"] else "preview"))

@app.route("/admin/r/<int:rid>/rfq", methods=["POST"])
def send_rfq(rid):
    with db() as c:
        r = c.execute("SELECT * FROM requests WHERE id=?", (rid,)).fetchone()
        chosen = request.form.getlist("insurer")
        manual = [l.strip() for l in request.form.get("manual", "").split("\n") if l.strip()]
        targets = [(n, insurer_email(n)) for n in chosen]
        for line in manual:
            name, _, email = line.partition("<")
            email = email.replace(">", "").strip() or insurer_email(name)
            targets.append((name.strip(), email))
        for name, email in targets:
            token = secrets.token_urlsafe(16)
            c.execute("INSERT INTO rfq(request_id, insurer, email, token, sent_at, status)"
                      " VALUES(?,?,?,?,?,?)",
                      (rid, name, email, token,
                       dt.datetime.now().isoformat(timespec="seconds"),
                       send_or_queue(rfq_email(r, name, email, token),
                                     f"{r['ref']}-{name.replace(' ', '_')}")))
        c.execute("UPDATE requests SET status='awaiting insurers' WHERE id=?", (rid,))
    flash(f"{len(targets)} quotation request(s) prepared for {r['ref']}.")
    return redirect(url_for("detail", rid=rid))

@app.route("/insurer/<token>", methods=["GET", "POST"])
def insurer(token):
    with db() as c:
        rfq = c.execute("SELECT * FROM rfq WHERE token=?", (token,)).fetchone()
        if not rfq:
            abort(404)
        r = c.execute("SELECT * FROM requests WHERE id=?", (rfq["request_id"],)).fetchone()
        if request.method == "POST":
            f = request.form
            c.execute("""INSERT INTO quotes(rfq_id, request_id, insurer, premium, excess,
                         sum_insured, exclusions, terms, adjuster_value, service_rating,
                         breadth, received) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                      (rfq["id"], r["id"], rfq["insurer"],
                       float(f.get("premium") or 0), float(f.get("excess") or 0),
                       float(f.get("sum_insured") or r["sum_insured"]), f.get("exclusions"),
                       f.get("terms"), float(f.get("adjuster_value") or r["adjuster_value"] or 0),
                       int(f.get("service_rating") or 3), int(f.get("breadth") or 3),
                       dt.datetime.now().isoformat(timespec="seconds")))
            c.execute("UPDATE rfq SET status='responded' WHERE id=?", (rfq["id"],))
            c.execute("UPDATE requests SET status='terms received' WHERE id=?", (r["id"],))
            return render_template("insurer_done.html", insurer=rfq["insurer"])
    return render_template("insurer.html", r=r, rfq=rfq, token=token)

@app.route("/admin/clients", methods=["GET", "POST"])
def admin_clients():
    with db() as c:
        if request.method == "POST":
            c.execute("INSERT INTO clients(name, sector, since, note) VALUES(?,?,?,?)",
                      (request.form.get("name"), request.form.get("sector"),
                       request.form.get("since"), request.form.get("note")))
            if request.form.get("clear") == "yes":
                c.execute("DELETE FROM clients WHERE name LIKE 'Sample%'")
            return redirect(url_for("admin_clients"))
        rows = c.execute("SELECT * FROM clients ORDER BY since").fetchall()
    return render_template("admin_clients.html", rows=rows)

@app.route("/admin/r/<int:rid>/report.pdf")
def report_pdf(rid):
    with db() as c:
        r = c.execute("SELECT * FROM requests WHERE id=?", (rid,)).fetchone()
        qs = [dict(q) for q in c.execute("SELECT * FROM quotes WHERE request_id=?", (rid,))]
    return send_file(build_report(r, score_quotes(qs, r["adjuster_value"])),
                     mimetype="application/pdf",
                     download_name=f"Alfam-Broker-Report-{r['ref']}.pdf")

@app.route("/admin/r/<int:rid>/send-report")
def send_report(rid):
    with db() as c:
        r = c.execute("SELECT * FROM requests WHERE id=?", (rid,)).fetchone()
        qs = [dict(q) for q in c.execute("SELECT * FROM quotes WHERE request_id=?", (rid,))]
    buf = build_report(r, score_quotes(qs, r["adjuster_value"]))
    path = os.path.join(OUTBOX, f"Alfam-Broker-Report-{r['ref']}.pdf")
    with open(path, "wb") as f:
        f.write(buf.read())
    state = send_or_queue(report_email(r, path), f"{r['ref']}-client-report")
    with db() as c:
        c.execute("UPDATE requests SET status=? WHERE id=?", ("report sent", rid))
    flash(f"Broker report {state} to {r['client_email']}" +
          ("" if state == "sent" else " (preview mode — .eml saved in /outbox)"))
    return redirect(url_for("detail", rid=rid))

@app.route("/sitemap.xml")
def sitemap():
    pages = ["", "about", "services", "clients", "quote", "claims", "contact", "privacy"]
    xml = ['<?xml version="1.0" encoding="UTF-8"?>', "<urlset xmlns='http://www.sitemaps.org/schemas/sitemap/0.9'>"]
    for p in pages:
        xml.append(f"<url><loc>{urljoin(PUBLIC_BASE, '/' + p)}</loc></url>")
    xml.append("</urlset>")
    return app.response_class("\n".join(xml), mimetype="application/xml")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8000)), debug=False)

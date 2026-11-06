"""Navigator (L0/L1) content: general, process-level information for matters the AI does not decide.

IMPORTANT: this is general orientation, written conservatively and NOT yet reviewed by a lawyer (`reviewed` is False for every
guide). It never states limitation periods, fees, or outcomes, and never says whether a person is likely to win. Each guide
tells the reader to confirm the forum and time limits with a lawyer or the District Legal Services Authority. A lawyer's
review is a precondition for raising any of these above L1 or for removing the disclaimer.
"""
from __future__ import annotations

DISCLAIMER = (
    "General information only, not legal advice. Time limits, courts and procedures "
    "depend on your facts, the amount and your location: confirm them with a lawyer or your District Legal Services "
    "Authority, which can arrange free legal aid for eligible people."
)

COMMON_DOCUMENTS = [
    "Government ID and proof of address",
    "Every document, message, photo, receipt or record connected to the matter, with dates",
    "Names and contact details of the people involved and any witnesses",
    "A copy of any notice, order, summons or complaint you have received or sent",
]

CANNOT_DO = [
    "Decide who is right or wrong",
    "Predict the outcome or the chance of success",
    "Advise you on what to plead, admit or sign",
    "Replace a lawyer where one is needed",
]

# family, level (0 or 1), forum text, steps. Wording is deliberately generic.
GUIDES: list[dict] = [
    {"id": "criminal_complaint", "label": "Criminal complaint or FIR", "family": "criminal", "level": 0,
     "summary": "How a criminal complaint usually starts, and what to prepare.",
     "forum": "Criminal matters usually begin with a report to the police or a complaint to a Magistrate. If anyone is in danger, contact the police or emergency services first.",
     "steps": ["Write down what happened, with dates, places and names", "Keep any evidence safe (messages, photos, medical or other records)",
               "Report to the police station, or ask a lawyer about a complaint to a Magistrate", "Ask for a copy of what you file and note the reference number",
               "Speak to a lawyer or legal aid before making any statement about someone else's guilt"]},
    {"id": "bail_and_custody", "label": "Arrest, bail and custody", "family": "criminal", "level": 0,
     "summary": "What to understand if you or a family member has been arrested or is facing arrest.",
     "forum": "Bail and custody are decided by courts. A lawyer is essential here; legal aid is available for those who cannot afford one.",
     "steps": ["Contact a lawyer or the legal aid authority immediately", "Note the date, time and place of arrest and the police station",
               "Collect identity and address documents and any papers the police gave", "Do not sign anything you do not understand"]},
    {"id": "cyber_crime_complaint", "label": "Cyber crime or online fraud", "family": "criminal", "level": 1,
     "summary": "Preparing the details and evidence for an online fraud or cyber crime report.",
     "forum": "Online fraud and cyber crime can be reported to the police and through the national cybercrime reporting channel; act quickly, and tell your bank at once if money was taken.",
     "steps": ["Contact your bank or payment provider immediately if money moved", "Save screenshots, transaction IDs, phone numbers, links and chat records",
               "Do not delete the accounts or messages involved", "File the report and keep the acknowledgement number"]},
    {"id": "family_matrimonial", "label": "Family and matrimonial matters", "family": "family", "level": 1,
     "summary": "Preparing for divorce, maintenance, custody or similar family matters.",
     "forum": "Family matters are usually heard by family courts or the civil courts, depending on the matter and location. Mediation is often encouraged. If there is violence or threat, contact the police or a helpline first.",
     "steps": ["Collect marriage, birth and property documents", "Write a short timeline of events", "List income, expenses and assets for any maintenance question",
               "Ask a lawyer or legal aid about mediation and the right forum"]},
    {"id": "succession_inheritance", "label": "Inheritance and succession", "family": "civil_other", "level": 1,
     "summary": "Preparing documents for inheritance, wills and succession questions.",
     "forum": "Inheritance matters can involve civil courts and revenue authorities, depending on the property and the document (for example a will). A lawyer can confirm the right forum.",
     "steps": ["Collect the death certificate, any will, and proof of relationship", "List the property and bank accounts involved, with document numbers",
               "Note who the legal heirs are and whether anyone disputes the claim", "Ask a lawyer which certificate or proceeding is needed"]},
    {"id": "motor_accident_claim", "label": "Motor accident claim", "family": "civil_other", "level": 1,
     "summary": "Preparing documents for an accident injury or death claim.",
     "forum": "Motor accident claims are heard by Motor Accident Claims Tribunals. A lawyer or legal aid can help with filing.",
     "steps": ["Collect the police report or FIR, medical records and bills", "Keep the vehicle and insurance details of everyone involved",
               "Keep proof of income and any loss of work", "Ask a lawyer about the tribunal and the time limit"]},
    {"id": "labour_industrial", "label": "Labour and industrial disputes", "family": "civil_other", "level": 1,
     "summary": "Preparing documents for workplace disputes beyond simple unpaid wages.",
     "forum": "Workplace disputes may go to labour authorities, labour courts or tribunals depending on the claim. Simple unpaid-wage claims may also fit the employment dispute type.",
     "steps": ["Collect the appointment letter, payslips, bank statements and attendance records", "Keep any termination notice or messages from the employer",
               "Write a timeline of what happened", "Ask a lawyer or labour authority which route applies"]},
    {"id": "tax_dispute", "label": "Tax notices and disputes", "family": "tax", "level": 0,
     "summary": "How to read and respond to a tax notice.",
     "forum": "Tax notices are handled by tax authorities, with appeals to higher authorities and tribunals. Notices carry deadlines: do not ignore them.",
     "steps": ["Note the date, section and deadline on the notice", "Collect the returns, assessments and proofs it refers to",
               "Speak to a tax professional or lawyer before replying"]},
    {"id": "service_matter", "label": "Government service matters", "family": "service", "level": 0,
     "summary": "Understanding disputes about government employment (promotion, transfer, dismissal, pension).",
     "forum": "Service disputes may go to administrative tribunals or the High Court depending on the employer. A lawyer can confirm which applies.",
     "steps": ["Collect the order or letter you are challenging and your service records", "Note the dates, because time limits are strict",
               "Ask a lawyer about the right forum and any internal remedy to try first"]},
    {"id": "constitutional_writ", "label": "Writ and constitutional matters", "family": "constitutional", "level": 0,
     "summary": "Understanding when and where a writ petition is considered.",
     "forum": "Writ petitions are heard by the High Courts and the Supreme Court. These matters need a lawyer.",
     "steps": ["Identify the government action or inaction you are concerned about", "Collect the orders, letters and representations you made",
               "Consult a lawyer: the choice of remedy and court is a legal decision"]},
    {"id": "ip_dispute", "label": "Intellectual property", "family": "ip", "level": 0,
     "summary": "Orientation for trademark, copyright, patent and design matters.",
     "forum": "IP matters involve the relevant registry or office and, for disputes, the courts. Registration and deadlines matter.",
     "steps": ["Collect proof of creation or first use, registrations and sales records", "Save evidence of the other party's use",
               "Consult an IP lawyer before sending any notice"]},
    {"id": "commercial_arbitration", "label": "Commercial and arbitration matters", "family": "civil_other", "level": 1,
     "summary": "Preparing documents for business disputes and arbitration clauses.",
     "forum": "Business contracts often name an arbitration or court forum. Check the contract's dispute clause first; a lawyer can confirm the route.",
     "steps": ["Find the contract and its dispute-resolution clause", "Collect invoices, correspondence and delivery or payment records",
               "Write a timeline of performance and breach", "Ask a lawyer whether arbitration or a court applies"]},
]


# Preparation guides for civil matters that also have a filing flow (or will). Keyed by the dispute-type id. They give a user
# something useful while the type is still behind its evaluation gate, and stay valid after it passes. Same shape as GUIDES, plus
# optional `documents` (type-specific, replaces the common list). Not in GUIDES: those become stand-alone navigator matters.
# Tenancy law is mostly state law (rent control Acts differ by state and by kind of tenancy), so nothing here states a notice
# period, limitation period or fee; it only says to confirm them.
COMPANION_GUIDES: dict[str, dict] = {
    "tenancy_dispute": {
        "summary": "Unpaid rent, a deposit that has not been returned, or a notice to leave: what to gather, and what to avoid.",
        "forum": "Claims for unpaid rent or a deposit are usually civil claims. Eviction and rent-control disputes may go to a special authority or court under your state's rent law, which differs by state and by the kind of tenancy. A lawyer or your District Legal Services Authority can confirm which applies.",
        "steps": ["Find the rent agreement or lease and read what it says about rent, the deposit, notice, and how the tenancy can end",
                  "List every payment with its date, amount and how it was paid, and keep the receipts or bank records",
                  "Keep every notice and message between landlord and tenant; if you send a notice, send it in writing and keep proof it was delivered",
                  "Note the dates the tenancy began, the rent fell due, and the property was vacated or handed back, and keep dated photos of its condition",
                  "Do not lock out a tenant, remove their belongings or cut off water or electricity, and do not stop paying rent without advice: these can create legal trouble for you",
                  "Ask a lawyer or legal aid whether a notice must come first, which forum applies and what the time limit is"],
        "documents": ["The rent agreement or lease, with any renewal, registration or stamp papers",
                      "Rent receipts and bank or UPI records of every payment, including any deposit",
                      "Photos or an inventory of the property's condition at move-in and move-out",
                      "A copy of any notice, demand or message about rent, repairs or leaving, sent or received",
                      "Government ID and proof of address for both sides, where you have them"],
    },
    "employment_dispute": {
        "summary": "Unpaid salary, notice pay or a final settlement that was never paid: what to gather, and where other workplace problems belong.",
        "forum": "Which forum hears a pay claim depends on who you are, what you claim and your state: some go to labour authorities or labour courts, others to the civil courts. Wrongful dismissal, harassment and similar workplace disputes are different and may need a labour authority or tribunal (see the guide on labour and industrial disputes). A lawyer, a labour office or your District Legal Services Authority can confirm the route.",
        "steps": ["Find your appointment or offer letter and any policy or contract that sets your pay, notice period and final settlement",
                  "List what you are owed month by month: the amount due, the date it fell due, and what was actually paid",
                  "Keep salary slips and bank statements, and any resignation, termination or relieving letter, with their dates",
                  "Save messages and emails in which the employer acknowledged the dues or promised to pay",
                  "If you send a written demand, keep a copy and proof of delivery, and do not exaggerate the amount",
                  "Ask a lawyer, a labour office or legal aid which route applies to you and what the time limit is before you file"],
        "documents": ["Your appointment or offer letter, and any contract or policy on pay and notice",
                      "Salary slips and bank statements showing what was paid",
                      "Your resignation, termination or relieving letter, and the date you left",
                      "Messages, emails or letters about the unpaid amount, sent or received",
                      "Your attendance records or any proof of the period you worked",
                      "Government ID and proof of address"],
    },
    "property_dispute": {
        "summary": "A boundary, encroachment, possession or unfinished-sale dispute over land or a building: what to gather, and what to avoid.",
        "forum": "Property disputes are usually heard by the civil courts, and some by revenue officers or special authorities depending on the land and your state. A request to stop someone urgently (an injunction or stay) is a court application that this tool does not handle: it sends such cases to a human. A lawyer or your District Legal Services Authority can confirm the forum.",
        "steps": ["Collect the title deed, sale agreement or other papers that show who owns or holds the property",
                  "Get the survey sketch, boundary or revenue records and the latest tax receipts for the land",
                  "Take dated photographs and notes of the disputed area, and of any construction or damage as it happens",
                  "Keep every message, notice and agreement with the other side, and any advance paid with proof of payment",
                  "Do not break down a wall, enter or take over the disputed land, or threaten anyone: this can turn a civil dispute into a criminal one",
                  "Ask a lawyer or legal aid early about urgent protection, the right forum and the time limit, because delay can cost you rights"],
        "documents": ["The title deed, sale deed or sale agreement, and any earlier chain of ownership papers",
                      "Survey sketch, boundary or revenue records (for example a record of rights) and property tax receipts",
                      "Dated photographs or video of the disputed area",
                      "Proof of any advance or payment made, and messages or notices exchanged with the other side",
                      "Government ID and proof of address"],
    },
}


def render(g: dict) -> dict:
    """The guide as served by the API (English), with the shared sections attached."""
    return {"summary": g["summary"], "forum": g["forum"], "steps": g["steps"], "documents": g.get("documents", COMMON_DOCUMENTS),
            "this_tool_cannot": CANNOT_DO, "disclaimer": DISCLAIMER, "reviewed": False, "language": "en"}


# ---------------------------------------------------------------------------------------------------------------------------
# Translations. One file per language in app/matters/translations/<code>.json:
#   {"shared": {"documents": [...], "this_tool_cannot": [...], "disclaimer": "..."},
#    "guides": {"<guide id>": {"label": "...", "summary": "...", "forum": "...", "steps": [...]}}}
# Same shape as the English source above, same list lengths. They are machine translations that no native speaker or lawyer has
# reviewed; the API says so (`translation_reviewed: false`) and the UI shows it. A missing language or guide falls back to English.
# ---------------------------------------------------------------------------------------------------------------------------
import json
from functools import lru_cache
from pathlib import Path

TRANSLATIONS_DIR = Path(__file__).resolve().parent / "translations"
TRANSLATION_CODES = ("hi", "ta", "te", "kn", "ml", "mr", "bn", "gu", "pa", "od")


def lang_code(lang: str | None) -> str:
    """'hi-IN' -> 'hi'; anything unknown or English -> 'en'."""
    code = (lang or "en").split("-")[0].strip().lower()
    return code if code in TRANSLATION_CODES else "en"


@lru_cache(maxsize=None)
def load_translation(code: str) -> dict | None:
    path = TRANSLATIONS_DIR / f"{code}.json"
    if code == "en" or not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def source_guide(guide_id: str) -> dict | None:
    return next((g for g in GUIDES if g["id"] == guide_id), None)


def localize(guide_id: str, base: dict, lang: str | None) -> dict:
    """The rendered English guide with translated text laid over it when a translation exists for (guide, language).
    `language` says what was actually served; `translation_reviewed` is always False for now."""
    code = lang_code(lang)
    tr = load_translation(code)
    entry = (tr or {}).get("guides", {}).get(guide_id)
    if not entry:
        return base
    shared = tr.get("shared", {})
    out = dict(base)
    out.update({"label": entry["label"], "summary": entry["summary"], "forum": entry["forum"], "steps": entry["steps"],
                "documents": shared.get("documents", base["documents"]), "this_tool_cannot": shared.get("this_tool_cannot", base["this_tool_cannot"]),
                "disclaimer": shared.get("disclaimer", base["disclaimer"]), "language": code, "translation_reviewed": False})
    return out


def localized_label_summary(guide_id: str, lang: str | None) -> tuple[str, str] | None:
    entry = ((load_translation(lang_code(lang)) or {}).get("guides", {})).get(guide_id)
    return (entry["label"], entry["summary"]) if entry else None

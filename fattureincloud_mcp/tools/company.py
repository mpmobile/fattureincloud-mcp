"""Company info and cost / revenue centers."""

import json

from .. import fic
from ..app import ann, tool
from ..lookups import fetch_cost_centers, fetch_revenue_centers


@tool(annotations=ann(read_only=True, idempotent=True))
def get_company_info() -> dict:
    """Info azienda collegata"""
    d = fic.companies_api.get_company_info(company_id=fic.COMPANY_ID).data.to_dict()
    info = d.get("info", d)
    return {"name": info.get("name"), "vat": info.get("vat_number"),
            "email": info.get("email"), "address": info.get("address_street"),
            "city": info.get("address_city"), "province": info.get("address_province")}


@tool(annotations=ann(read_only=True, idempotent=True))
def list_cost_centers() -> str:
    """Lista combinata di centri di costo e ricavo configurati in FattureInCloud. L'API FIC espone due
    liste separate (cost_centers per documenti ricevuti, revenue_centers per documenti emessi); questo
    tool ne ritorna l'unione deduplicata e ordinata, coerente con la vista 'Analisi centri c/r' della
    UI FIC. Le validation interne dei tool di mutazione sono type-specific:
    create_invoice/credit_note/proforma/update_document/duplicate_invoice/convert_proforma_to_invoice
    validano `revenue_center` contro la sola lista revenue_centers; create_received_document valida
    `cost_center` contro la sola lista cost_centers. Read-only."""
    cost = fetch_cost_centers(company_id=fic.COMPANY_ID)
    revenue = fetch_revenue_centers(company_id=fic.COMPANY_ID)
    # Serialized here: FastMCP would turn a returned list into one content block per item.
    return json.dumps(sorted(set(cost) | set(revenue)), indent=2, ensure_ascii=False)

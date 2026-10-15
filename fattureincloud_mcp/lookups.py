"""Cached lookups of reference data: clients, cost and revenue centers."""

from datetime import timedelta

from . import cache, fic


def get_client_by_id(client_id, *, company_id=None):
    if company_id is None:
        company_id = fic.COMPANY_ID
    resource = f"client_{client_id}"
    hit = cache.get(resource, company_id, ttl=timedelta(hours=24))
    if hit is not None:
        return hit
    try:
        response = fic.clients_api.get_client(company_id=company_id, client_id=client_id)
        data = response.data.to_dict()
        cache.put(resource, company_id, data)
        return data
    except Exception:
        return None


def get_ei_code_for_client(client_id, *, company_id=None):
    if company_id is None:
        company_id = fic.COMPANY_ID
    try:
        client = get_client_by_id(client_id, company_id=company_id)
        if client:
            ei_code = (client.get('ei_code') or '').strip()
            if ei_code:
                return ei_code
            pec = (client.get('certified_email') or '').strip()
            if pec:
                return '0000000'
        return '0000000'
    except Exception:
        return '0000000'


@cache.cached("cost_centers", ttl=timedelta(hours=24))
def fetch_cost_centers(*, company_id):
    """Cost centers (FIC `/info/cost_centers`). Used to validate `cost_center`
    on received documents only."""
    try:
        response = fic.info_api.list_cost_centers(company_id=company_id)
        return list(response.data or [])
    except Exception:
        return []


@cache.cached("revenue_centers", ttl=timedelta(hours=24))
def fetch_revenue_centers(*, company_id):
    """Revenue centers (FIC `/info/revenue_centers`). Used to validate
    `revenue_center` on issued documents only.

    FIC keeps cost and revenue centers as two separate registries; the
    `list_cost_centers` MCP tool returns their union (matching the
    'Analisi centri c/r' view in the FIC UI), but mutation validation
    has to use the type-specific list to match how the FIC API itself
    accepts/rejects values."""
    try:
        response = fic.info_api.list_revenue_centers(company_id=company_id)
        return list(response.data or [])
    except Exception:
        return []


def build_entity_from_client(client_id, client_data=None):
    if not client_data:
        client_data = get_client_by_id(client_id)
    if not client_data:
        return None
    ei_code = get_ei_code_for_client(client_id)
    entity = {
        "id": client_id,
        "name": client_data.get("name", ""),
        "vat_number": client_data.get("vat_number", ""),
        "tax_code": client_data.get("tax_code", ""),
        "address_street": client_data.get("address_street", ""),
        "address_city": client_data.get("address_city", ""),
        "address_postal_code": client_data.get("address_postal_code", ""),
        "address_province": client_data.get("address_province", ""),
        "country": client_data.get("country", "Italia"),
        "ei_code": ei_code,
    }
    pec = (client_data.get("certified_email") or "").strip()
    if pec:
        entity["certified_email"] = pec
    return entity

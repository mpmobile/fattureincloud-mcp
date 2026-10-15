"""Client registry: list, create, update."""

from typing import Annotated

from pydantic import Field

from .. import fic
from ..app import ann, tool
from ..listing import DEFAULT_MAX_RESULTS, fetch_all_pages, q_escape
from ..lookups import get_client_by_id



@tool(annotations=ann(read_only=True, idempotent=True))
def list_clients(
    query: Annotated[str | None, Field(description="Filtro nome/ragione sociale, ricerca parziale (opzionale)")] = None,
    vat_number: Annotated[str | None, Field(description="Partita IVA esatta (opzionale)")] = None,
    max_results: Annotated[int | None, Field(description=f"Massimo clienti restituiti (default {DEFAULT_MAX_RESULTS})")] = None,
) -> dict:
    """Lista clienti (paginazione automatica). La risposta indica total e truncated."""
    clauses = []
    if query:
        clauses.append(f"name like '%{q_escape(query)}%'")
    if vat_number:
        clauses.append(f"vat_number = '{q_escape(vat_number)}'")
    rows, total = fetch_all_pages(
        fic.clients_api.list_clients, max_results=max_results or DEFAULT_MAX_RESULTS,
        q=" and ".join(clauses) or None, sort="name",
    )
    clients = [{"id": cd.get("id"), "name": cd.get("name"),
                "vat": cd.get("vat_number"), "tax_code": cd.get("tax_code"),
                "email": cd.get("email")} for cd in rows]
    return {"total": total, "returned": len(clients),
            "truncated": len(clients) < total, "clients": clients}


@tool(annotations=ann())
def create_client(
    name: Annotated[str, Field(description="Nome/Ragione sociale")],
    vat_number: Annotated[str, Field(description="Partita IVA (opzionale)")] = "",
    tax_code: Annotated[str, Field(description="Codice fiscale (opzionale)")] = "",
    ei_code: Annotated[str, Field(description="Codice destinatario SDI (opzionale)")] = "",
    certified_email: Annotated[str, Field(description="PEC (opzionale)")] = "",
    email: Annotated[str, Field(description="Email ordinaria (opzionale)")] = "",
    address_street: Annotated[str, Field(description="Indirizzo (opzionale)")] = "",
    address_city: Annotated[str, Field(description="Città (opzionale)")] = "",
    address_postal_code: Annotated[str, Field(description="CAP (opzionale)")] = "",
    address_province: Annotated[str, Field(description="Provincia (opzionale)")] = "",
    country: Annotated[str, Field(description="Paese (default: Italia)")] = "Italia",
    phone: Annotated[str, Field(description="Telefono (opzionale)")] = "",
) -> dict:
    """Crea nuovo cliente in anagrafica"""
    client_data = {
        "name": name, "vat_number": vat_number, "tax_code": tax_code, "ei_code": ei_code,
        "certified_email": certified_email, "email": email, "address_street": address_street,
        "address_city": address_city, "address_postal_code": address_postal_code,
        "address_province": address_province, "country": country, "phone": phone,
    }
    d = fic.clients_api.create_client(
        company_id=fic.COMPANY_ID, create_client_request={"data": client_data}
    ).data.to_dict()
    return {
        "success": True,
        "id": d.get("id"),
        "name": d.get("name"),
        "vat_number": d.get("vat_number"),
        "message": f"Cliente '{d.get('name')}' creato con ID {d.get('id')}."
    }


@tool(annotations=ann(idempotent=True))
def update_client(
    client_id: Annotated[int, Field(description="ID cliente")],
    name: Annotated[str | None, Field(description="Nome/Ragione sociale (opzionale)")] = None,
    vat_number: Annotated[str | None, Field(description="Partita IVA (opzionale)")] = None,
    tax_code: Annotated[str | None, Field(description="Codice fiscale (opzionale)")] = None,
    ei_code: Annotated[str | None, Field(description="Codice destinatario SDI (opzionale)")] = None,
    certified_email: Annotated[str | None, Field(description="PEC (opzionale)")] = None,
    email: Annotated[str | None, Field(description="Email ordinaria (opzionale)")] = None,
    address_street: Annotated[str | None, Field(description="Indirizzo (opzionale)")] = None,
    address_city: Annotated[str | None, Field(description="Città (opzionale)")] = None,
    address_postal_code: Annotated[str | None, Field(description="CAP (opzionale)")] = None,
    address_province: Annotated[str | None, Field(description="Provincia (opzionale)")] = None,
    phone: Annotated[str | None, Field(description="Telefono (opzionale)")] = None,
) -> dict:
    """Aggiorna dati cliente esistente. Passa solo i campi da modificare."""
    orig = get_client_by_id(client_id)
    if not orig:
        return {"success": False, "error": f"Cliente {client_id} non trovato"}
    updates = {
        "name": name, "vat_number": vat_number, "tax_code": tax_code, "ei_code": ei_code,
        "certified_email": certified_email, "email": email, "address_street": address_street,
        "address_city": address_city, "address_postal_code": address_postal_code,
        "address_province": address_province, "phone": phone,
    }
    # Fields not passed keep their current value, so the PUT does not blank them.
    client_data = {f: v if v is not None else orig.get(f) for f, v in updates.items()
                   if v is not None or orig.get(f) is not None}
    d = fic.clients_api.modify_client(
        company_id=fic.COMPANY_ID, client_id=client_id, modify_client_request={"data": client_data}
    ).data.to_dict()
    return {
        "success": True,
        "id": d.get("id"),
        "name": d.get("name"),
        "message": f"Cliente '{d.get('name')}' aggiornato con successo."
    }

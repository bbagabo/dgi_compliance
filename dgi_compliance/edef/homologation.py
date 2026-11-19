"""Homologated scope of the SFE (Note de service n°01/220/DGI/DG/DLCFI/FMM/LLM/2026, VI.2) - v5.5.1.

  "L'homologation d'un SFE porte exclusivement sur la version, les fonctionnalites, les types de factures,
   les groupes de taxation et les modalites d'interconnexion effectivement presentes [...] il ne peut etre
   utilise que pour les types de factures et les groupes de taxation expressement couverts par son
   homologation."

Before the homologation (status "Non homologue (essais)") nothing is restricted: the campaign must be able
to present every group and every invoice type. Once the attestation is recorded (status "Homologue"), a
normalization outside the covered invoice types or tax groups is refused BEFORE anything is sent to the
MCF or the e-MCF. Pure functions (no database): tested without a bench.
"""
from dgi_compliance.edef.calc import APPLICABLE_GROUPS, RESERVED_GROUPS

HOMOLOGATED = "Homologue"
NOT_HOMOLOGATED = "Non homologue (essais)"
INVOICE_TYPES = ("FV", "FT", "FA", "EV", "ET", "EA")


def parse_groups(text) -> str:
    """'A B, c;Q' -> 'ABCQ' (order A-S, no duplicate). ValueError on a reserved or unknown letter."""
    letters = [c for c in str(text or "").upper() if c.isalpha()]
    reserved = sorted({c for c in letters if c in RESERVED_GROUPS})
    unknown = sorted({c for c in letters if c not in APPLICABLE_GROUPS and c not in RESERVED_GROUPS})
    if reserved:
        raise ValueError("groupes reserves par la DGI (T a Z) : " + "".join(reserved))
    if unknown:
        raise ValueError("lettres inconnues : " + "".join(unknown))
    return "".join(g for g in APPLICABLE_GROUPS if g in letters)


def parse_types(text) -> list:
    """'fv, FA ev' -> ['FV', 'FA', 'EV'] (order of the specification). ValueError on an unknown type."""
    raw = [t for t in str(text or "").upper().replace(";", ",").replace(" ", ",").split(",") if t]
    unknown = sorted({t for t in raw if t not in INVOICE_TYPES})
    if unknown:
        raise ValueError("types de facture inconnus : " + ", ".join(unknown))
    return [t for t in INVOICE_TYPES if t in raw]


def scope_errors(dto, status, groups, types) -> list:
    """Reasons why this e-MCF request is outside the homologated scope (empty list = allowed)."""
    if (status or "") != HOMOLOGATED:
        return []
    errors = []
    try:
        allowed_types = parse_types(types)
        allowed_groups = parse_groups(groups)
    except ValueError as e:
        return [f"perimetre homologue mal renseigne dans DGI Compliance Settings ({e})"]
    if allowed_types and dto.get("type") not in allowed_types:
        errors.append(f"type de facture {dto.get('type')} non couvert par l'attestation d'homologation "
                      f"(types homologues : {', '.join(allowed_types)})")
    if allowed_groups:
        outside = sorted({str(it.get("taxGroup") or "?") for it in dto.get("items") or []
                          if it.get("taxGroup") not in allowed_groups})
        if outside:
            errors.append(f"groupe(s) {''.join(outside)} non couvert(s) par l'attestation d'homologation "
                          f"(groupes homologues : {allowed_groups}) ; une extension demande une re-homologation "
                          "(Note de service n°01/220, VI.2)")
    return errors

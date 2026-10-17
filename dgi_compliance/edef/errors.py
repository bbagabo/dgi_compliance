"""e-MCF error codes (API e-MCF v1.3, annexe "Liste d'erreurs") -> clear French messages."""

EMCF_ERRORS = {
    "1": "Le nombre maximum de factures en attente est depasse (10). Reessayez dans 2 minutes.",
    "3": "Type de facture non valide.",
    "4": "La reference de la facture originale est manquante.",
    "5": "La longueur de la reference de la facture originale est incorrecte.",
    "7": "Le type de paiement n'est pas valide.",
    "8": "La facture doit contenir des articles.",
    "9": "Le groupe de taxation d'un article n'est pas valide.",
    "10": "La reference de la facture originale ne peut pas etre validee, reessayez plus tard.",
    "11": "Erreur de validation de la facture d'avoir.",
    "12": "Reference d'origine invalide: le montant de l'avoir depasse celui de la facture originale.",
    "13": "Les informations de l'operateur sont manquantes.",
    "14": "Le montant de la facture est trop eleve.",
    "15": "Le nom de l'article ne peut pas etre vide.",
    "16": "Le montant de la facture ne peut pas etre 0.",
    "17": "Le montant du paiement est trop eleve.",
    "18": "Quantite ou prix negatif non autorise.",
    "19": "Quantite et prix ne peuvent pas etre 0.",
    "20": "La facture n'existe pas ou elle est deja finalisee / annulee.",
    "21": "La valeur du champ taxSpecificValue est invalide.",
    "22": "taxSpecificValue est obligatoire lorsque taxSpecificAmount est precise.",
    "23": "Le champ taxSpecificAmount ne peut pas etre negatif.",
    "24": "Le type d'article n'est pas valide.",
    "25": "Le type de client n'est pas valide.",
    "26": "taxSpecificValue: valeur unitaire ou pourcentage se terminant par %.",
    "27": "Type de facture d'avoir non valide.",
    "31": "La reference de la facture ne peut pas etre vide.",
    "32": "ISF ne peut pas etre vide.",
    "33": "Le mode de prix doit etre ttc ou ht.",
    "41": "Le nom (denomination) du client est obligatoire.",
    "42": "Le NIF du client est obligatoire.",
    "50": "Le montant TTC declare ne correspond pas a la valeur calculee.",
    "51": "Le montant TVA declare ne correspond pas a la valeur calculee.",
    "60": "La facture d'avoir n'a pas pu etre validee: serveur DGI inaccessible.",
    "61": "Erreur lors de la normalisation de la facture.",
    "63": "La facture existe deja dans les factures en attente.",
    "99": "Erreur lors du traitement de la demande.",
}

# Errors worth retrying later (temporary condition on the e-MCF side).
RETRYABLE = {"1", "10", "60", "61", "99"}


def describe(data, fallback=None) -> str:
    """'[e-MCF 12] Reference d'origine invalide ...' from a response body, else fallback."""
    if isinstance(data, dict) and data.get("errorCode") not in (None, "", 0, "0"):
        code = str(data.get("errorCode"))
        text = EMCF_ERRORS.get(code) or ""
        desc = (data.get("errorDesc") or "").strip()
        if desc and desc not in text:
            text = f"{text} ({desc})" if text else desc
        return f"[e-MCF {code}] {text}".strip()
    return fallback or ""


def is_retryable(result) -> bool:
    """Network failure / 5xx / temporary e-MCF error -> a later retry may succeed."""
    if not result:
        return True
    status = result.get("status") or 0
    if status == 0 or status >= 500:
        return True
    data = result.get("data")
    return isinstance(data, dict) and str(data.get("errorCode")) in RETRYABLE

"""Account-holder brokerage-class reads and guarded Avanza requests."""

from typing import Any

from avanza_mcp.avanza_ext import avanza_private_get, avanza_private_post


COURTAGE_CLASS_PATH = "/_api/trading/courtageclass/courtageclass/"
COURTAGE_CLASS_UPDATE_PATH = "/_api/trading/courtageclass/courtageclass/update/"
DERIVATIVE_ORDER_PATH = "/_api/trading/courtageclass/derivativeorderexists"
SESSION_INFO_PATH = "/_api/authentication/session/info/session"

CLASS_NAMES = {
    "START": "Start",
    "MINI": "Mini",
    "SMALL": "Small",
    "MEDIUM": "Medium",
    "FASTPRIS": "Fixed Price",
    "PRIVATE_BANKING_MINI": "PB Mini",
    "PRIVATE_BANKING": "PB",
    "PRIVATE_BANKING_FASTPRIS": "PB Fixed Price",
    "PRO5": "Pro 5",
}
NORMAL_CLASSES = ("MINI", "SMALL", "MEDIUM", "FASTPRIS")
PRIVATE_BANKING_CLASSES = (
    "PRIVATE_BANKING_MINI",
    "PRIVATE_BANKING",
    "PRIVATE_BANKING_FASTPRIS",
)
PERSONNEL_CLASSES = ("PRIVATE_BANKING_MINI", "PRO5", "PRIVATE_BANKING_FASTPRIS")
CLASS_CHOICES_BY_CUSTOMER_GROUP = {
    "BRONS": NORMAL_CLASSES,
    "SILVER": NORMAL_CLASSES,
    "GULD": NORMAL_CLASSES,
    "PLATINA": NORMAL_CLASSES,
    "PRIVATE_BANKING": PRIVATE_BANKING_CLASSES,
    "PERSONNEL": PERSONNEL_CLASSES,
    "PRO1": (),
    "PRO2": (),
    "PRO3": (),
    "PRO4": (),
    "PRO5": (),
    "SPECIAL": (),
}


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"Avanza {label} response must be an object.")
    return value


def normalize_courtage_class_data(
    class_data: Any,
    session_data: Any,
    derivative_data: Any,
) -> dict[str, Any]:
    class_data = _object(class_data, "courtage class")
    session_data = _object(session_data, "session info")
    derivative_data = _object(derivative_data, "derivative order")
    user = _object(session_data.get("user"), "session user")
    if user.get("loggedIn") is not True:
        raise PermissionError("Avanza session is not confirmed logged in.")

    current_class = str(class_data.get("currentCourtageClass") or "").strip().upper()
    if not current_class:
        raise ValueError("Avanza did not return the current courtage class.")
    customer_group = str(user.get("customerGroup") or "").strip().upper()
    if "hasDerivatives" not in derivative_data or not isinstance(derivative_data["hasDerivatives"], bool):
        raise ValueError("Avanza did not confirm derivative-order eligibility.")

    available_codes = CLASS_CHOICES_BY_CUSTOMER_GROUP.get(customer_group)
    if available_codes is not None and class_data.get("eligibleForStart") is True:
        available_codes = ("START", *available_codes)
    special_agreement = class_data.get("specialAgreement") is True
    if special_agreement:
        available_codes = ()

    return {
        "current_class": current_class,
        "current_class_name": CLASS_NAMES.get(current_class, current_class),
        "customer_group": customer_group or None,
        "available_classes": (
            [{"code": code, "name": CLASS_NAMES.get(code, code)} for code in available_codes]
            if available_codes is not None else []
        ),
        "available_classes_verified": available_codes is not None,
        "eligible_for_start": class_data.get("eligibleForStart") is True,
        "special_agreement": special_agreement,
        "derivative_order_exists": derivative_data["hasDerivatives"],
        "can_change": bool(available_codes) and not derivative_data["hasDerivatives"],
        "scope": "Account holder; applies to all owned accounts, not joint or power-of-attorney accounts.",
        "effective_for": "New orders immediately; existing orders keep their original class.",
    }


def read_courtage_class(avanza: Any) -> dict[str, Any]:
    return normalize_courtage_class_data(
        avanza_private_get(avanza, COURTAGE_CLASS_PATH),
        avanza_private_get(avanza, SESSION_INFO_PATH),
        avanza_private_get(avanza, DERIVATIVE_ORDER_PATH),
    )


def submit_courtage_class_change(avanza: Any, target_class: str) -> Any:
    return avanza_private_post(avanza, COURTAGE_CLASS_UPDATE_PATH, {"newClass": target_class})

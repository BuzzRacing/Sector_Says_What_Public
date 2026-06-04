"""
Driver Profile Assignment — Sector Says What
Assigns each race driver a persistent F1 team color, gender-guessed icon,
and profile image for the duration of the race.
"""

import json
import os
import random
import hashlib

import race_config

EXPORTS_DIR = os.path.join(os.path.dirname(__file__), "sector_said_exports")
PROFILES_FILE = os.path.join(EXPORTS_DIR, "driver_profiles.json")

# ── Default Profile Images ───────────────────────────────────────
DRIVERS_IMG_DIR = "python/sector_says_oversee/drivers"
DRIVERS_IMG_ABS = os.path.join(os.path.dirname(__file__), "sector_says_oversee", "drivers")
OWNER_IMAGE = f"{DRIVERS_IMG_DIR}/tony_yo.png"
FALLBACK_IMAGES = {
    "male":    f"{DRIVERS_IMG_DIR}/default_male.png",
    "female":  f"{DRIVERS_IMG_DIR}/default_female.png",
    "unknown": f"{DRIVERS_IMG_DIR}/default.png",
}


def _scan_default_images():
    """Scan the drivers folder for numbered default images.
    Returns {"male": [...], "female": [...]} sorted lists of relative paths.
    Falls back to the single default if no numbered variants exist."""
    pools = {"male": [], "female": []}
    try:
        for fn in sorted(os.listdir(DRIVERS_IMG_ABS)):
            lower = fn.lower()
            if lower.startswith("default_male_") and lower.endswith(".png"):
                pools["male"].append(f"{DRIVERS_IMG_DIR}/{fn}")
            elif (lower.startswith("default_female_") or lower.startswith("default_woman_")) and lower.endswith(".png"):
                pools["female"].append(f"{DRIVERS_IMG_DIR}/{fn}")
    except OSError:
        pass
    return pools


def _find_custom_image(name: str) -> str | None:
    """Check if a custom image exists for this driver (e.g. alex_albon.png)."""
    slug = name.strip().lower().replace(" ", "_")
    for ext in (".png", ".jpg", ".jpeg", ".webp"):
        if os.path.isfile(os.path.join(DRIVERS_IMG_ABS, slug + ext)):
            return f"{DRIVERS_IMG_DIR}/{slug}{ext}"
    return None


def _pick_default_image(name: str, gender: str) -> str:
    """Pick a driver profile image: custom file > random numbered default > single fallback."""
    # 1. Custom image named after the driver (e.g. alex_albon.png)
    custom = _find_custom_image(name)
    if custom:
        return custom
    # 2. Random pick from numbered pool, deterministic per name
    pools = _scan_default_images()
    pool_key = gender if gender in pools else "male"
    pool = pools.get(pool_key, [])
    if pool:
        h = _name_hash(name)
        return pool[h % len(pool)]
    # 3. Single fallback
    return FALLBACK_IMAGES.get(gender, FALLBACK_IMAGES["unknown"])

# ── F1 Team Colors ────────────────────────────────────────────────
# Each opponent gets one of these assigned randomly (but deterministically per name)
F1_COLORS = [
    {"id": "ferrari",   "hex": "#DC0000", "name": "Ferrari"},
    {"id": "mclaren",   "hex": "#FF8700", "name": "McLaren"},
    {"id": "redbull",   "hex": "#3671C6", "name": "Red Bull"},
    {"id": "merc",      "hex": "#27F4D2", "name": "Mercedes"},
    {"id": "alpine",    "hex": "#0090FF", "name": "Alpine"},
    {"id": "haas",      "hex": "#B6BABD", "name": "Haas"},
    {"id": "am",        "hex": "#229971", "name": "Aston Martin"},
    {"id": "williams",  "hex": "#64C4FF", "name": "Williams"},
    {"id": "sauber",    "hex": "#52E252", "name": "Sauber"},
    {"id": "visa",      "hex": "#6692FF", "name": "Visa RB"},
]

# ── Gender Detection ─────────────────────────────────────────────
# Common female first names for iRacing drivers
FEMALE_NAMES = {
    "abby", "abigail", "ada", "adriana", "agnes", "aisha", "alex", "alexa",
    "alexandra", "alice", "alicia", "alina", "alison", "allison", "alyssa",
    "amanda", "amber", "amelia", "amy", "ana", "andrea", "angela", "angelina",
    "anita", "ann", "anna", "anne", "annie", "aria", "ariana", "ashley",
    "audrey", "aurora", "ava", "bailey", "barbara", "beatrice", "becky",
    "bella", "beth", "betty", "bianca", "blair", "bonnie", "brenda",
    "brianna", "bridget", "brittany", "brooke", "caitlin", "camila",
    "candice", "cara", "carly", "carmen", "carol", "caroline", "carolyn",
    "cassandra", "catherine", "cecilia", "charlotte", "chelsea", "cheryl",
    "chloe", "christina", "christine", "cindy", "claire", "clara", "claudia",
    "colleen", "courtney", "crystal", "cynthia", "daisy", "dana", "danielle",
    "daphne", "dawn", "debbie", "deborah", "denise", "diana", "diane",
    "donna", "dorothy", "eileen", "elaine", "elena", "elisa", "elizabeth",
    "ella", "ellen", "ellie", "emily", "emma", "erica", "erin", "esther",
    "eva", "evelyn", "faith", "fatima", "felicia", "fiona", "florence",
    "frances", "gabriella", "gabrielle", "gail", "gemma", "georgia",
    "gianna", "gina", "gloria", "grace", "greta", "hailey", "hannah",
    "harper", "harriet", "hayley", "hazel", "heather", "heidi", "helen",
    "hillary", "holly", "hope", "ida", "imogen", "ines", "ingrid", "irene",
    "iris", "isabel", "isabella", "ivy", "jacqueline", "jade", "jamie",
    "jane", "janet", "janice", "jasmine", "jean", "jenna", "jennifer",
    "jenny", "jessica", "jill", "joan", "joanna", "jocelyn", "jordan",
    "josephine", "joy", "joyce", "judith", "judy", "julia", "juliana",
    "julie", "june", "kaitlyn", "karen", "kate", "katherine", "kathleen",
    "kathryn", "kathy", "katie", "katrina", "kayla", "kelly", "kelsey",
    "kendra", "kerry", "kim", "kimberly", "kristen", "kristin", "kristina",
    "kylie", "lara", "laura", "lauren", "layla", "leah", "lena", "leslie",
    "lily", "linda", "lindsay", "lisa", "liz", "lorraine", "louise",
    "lucia", "lucy", "luna", "lydia", "lynn", "mackenzie", "madeline",
    "madison", "mae", "maggie", "maia", "mandy", "margaret", "maria",
    "mariana", "marie", "marilyn", "marina", "marissa", "marlene", "martha",
    "mary", "matilda", "maya", "megan", "melanie", "melissa", "melody",
    "mercedes", "mia", "michelle", "mikayla", "mila", "miranda", "miriam",
    "molly", "monica", "morgan", "nadia", "nancy", "naomi", "natalie",
    "natasha", "nicole", "nina", "nora", "olivia", "paige", "pamela",
    "patricia", "paula", "pauline", "penelope", "petra", "phoebe", "piper",
    "priscilla", "rachel", "rebecca", "regina", "renee", "riley", "rita",
    "roberta", "robin", "rosa", "rosalie", "rose", "rosemary", "roxanne",
    "ruby", "ruth", "sabrina", "sadie", "sally", "samantha", "sandra",
    "sara", "sarah", "savannah", "scarlett", "selena", "serena", "shannon",
    "sharon", "sheila", "shelby", "shirley", "sierra", "silvia", "simone",
    "skylar", "sofia", "sonia", "sophia", "sophie", "stacy", "stella",
    "stephanie", "sue", "summer", "susan", "suzanne", "sydney", "sylvia",
    "tamara", "tammy", "tanya", "tara", "tatiana", "taylor", "teresa",
    "tiffany", "tina", "tracy", "trinity", "valentina", "valerie",
    "vanessa", "vera", "veronica", "victoria", "violet", "virginia",
    "vivian", "wendy", "whitney", "willow", "yvonne", "zoe", "zoey",
}

# Names that are ambiguous (could be either gender)
AMBIGUOUS_NAMES = {
    "alex", "avery", "bailey", "blake", "cameron", "casey", "charlie",
    "chris", "corey", "dakota", "dallas", "devon", "drew", "dylan",
    "elliot", "emery", "finley", "frankie", "gray", "harley", "hayden",
    "hunter", "jamie", "jesse", "jordan", "kai", "kelly", "kendall",
    "kennedy", "lane", "lee", "logan", "london", "mackenzie", "marley",
    "morgan", "noel", "oakley", "parker", "pat", "peyton", "quinn",
    "reagan", "reese", "remy", "riley", "river", "robin", "rowan",
    "sage", "sam", "sawyer", "shay", "skylar", "sydney", "taylor",
    "terry", "tony", "tracy", "val", "winter",
}


def guess_gender(full_name: str) -> str:
    """
    Guess driver gender from name.
    Returns: 'female', 'male', or 'unknown'
    """
    if not full_name:
        return "unknown"

    # Extract first name
    name = full_name.strip()
    # Handle "Lastname, Firstname" format
    if "," in name:
        parts = name.split(",")
        first = parts[1].strip().split()[0] if len(parts) > 1 else parts[0]
    else:
        first = name.split()[0]

    first = first.lower().strip()

    # Remove common suffixes/prefixes
    for suffix in ("[pit]", "jr", "sr", "ii", "iii", "iv"):
        first = first.replace(suffix, "").strip()

    if not first:
        return "unknown"

    if first in AMBIGUOUS_NAMES:
        return "unknown"
    if first in FEMALE_NAMES:
        return "female"

    # Default to male for unrecognized names (most common in racing)
    return "male"


def _name_hash(name: str) -> int:
    """Deterministic hash from a name string."""
    return int(hashlib.md5(name.encode("utf-8")).hexdigest(), 16)


def _profile_name_key(profile: dict) -> str:
    return " ".join(str(profile.get("name") or "").strip().lower().split())


def _profile_cust_id(profile: dict) -> int | None:
    try:
        cid = profile.get("cust_id")
        return int(cid) if cid is not None else None
    except (TypeError, ValueError):
        return None


def _owner_profile_images() -> set[str]:
    out = {OWNER_IMAGE.replace("\\", "/").lower()}
    try:
        owners = race_config.cfg("owner_drivers") or {}
    except Exception:
        owners = {}
    for info in owners.values():
        if not isinstance(info, dict):
            continue
        img = str(info.get("profile_image") or "").strip()
        if img:
            out.add(img.replace("\\", "/").lower())
    return out


def _owner_lookup_by_cust_id() -> dict:
    """Build {cust_id: owner_info} from race_config.owner_drivers.

    The live leaderboard's `Owner` flag can miss the owner when iRacing
    doesn't tag the player's car (AI sessions, hotlaps, etc). Cross-
    checking by iracing_customer_id guarantees the owner is recognised
    regardless of the telemetry hint.
    """
    out = {}
    try:
        owners = race_config.cfg("owner_drivers") or {}
    except Exception:
        return out
    for key, info in owners.items():
        if not isinstance(info, dict):
            continue
        if info.get("is_active") is False:
            continue
        cid = info.get("iracing_customer_id")
        try:
            cid = int(cid) if cid is not None else None
        except (TypeError, ValueError):
            cid = None
        if cid is None:
            continue
        out[cid] = {
            "display_name": (info.get("display_name") or key or "").strip(),
            "profile_image": (info.get("profile_image") or "").strip(),
        }
    return out


def assign_driver_profiles(cars: list) -> dict:
    """
    Assign each driver in the grid a persistent F1 color, gender, and icon type.

    Args:
        cars: List of car dicts from race_grid.json (need CarIdx, Name, CarNumber)

    Returns:
        Dict mapping CarIdx (str) -> profile dict
    """
    profiles = {}
    used_colors = []

    owner_by_cid = _owner_lookup_by_cust_id()

    for car in cars:
        car_idx = str(car.get("CarIdx", ""))
        name = car.get("Name", "Unknown")
        car_num = car.get("CarNumber", "")
        is_owner = bool(car.get("Owner", False))

        # Cross-check against race_config.owner_drivers by cust_id so the
        # owner is always recognised even when the live leaderboard's
        # `Owner` flag is missing (common in AI sessions).
        cust_id_val = car.get("CustId")
        try:
            cust_id_int = int(cust_id_val) if cust_id_val is not None else None
        except (TypeError, ValueError):
            cust_id_int = None
        config_owner = owner_by_cid.get(cust_id_int) if cust_id_int is not None else None
        if config_owner:
            is_owner = True

        # Gender detection
        gender = guess_gender(name)

        # Color assignment — deterministic per name so it's stable across refreshes
        h = _name_hash(name)

        if is_owner:
            # Owner driver always gets gold
            color = {"id": "player", "hex": "#D4AF37", "name": "Sector Says What"}
        else:
            # Assign from F1 colors, try to spread them out
            color_idx = h % len(F1_COLORS)
            color = F1_COLORS[color_idx]

        icon = gender  # "male", "female", or "unknown"

        # Default profile image based on owner status / gender
        if is_owner:
            default_img = (config_owner and config_owner.get("profile_image")) or OWNER_IMAGE
        else:
            default_img = _pick_default_image(name, gender)

        profiles[car_idx] = {
            "car_idx": int(car_idx) if car_idx.isdigit() else 0,
            "name": name,
            "car_number": car_num,
            "gender": gender,
            "icon": icon,
            "profile_image": default_img,
            "color_id": color["id"],
            "color_hex": color["hex"],
            "color_name": color["name"],
            "is_owner": is_owner,
            # iRacing credentials — sourced from live_leaderboard's car dict,
            # which pulls them from the DriverInfo YAML. None if the engine
            # hasn't populated them yet (e.g. pre-session).
            "cust_id": car.get("CustId"),
            "irating": car.get("IRating"),
            "lic_string": car.get("LicString"),
            "lic_class": car.get("LicClass"),
            "lic_safety": car.get("LicSafety"),
            "lic_level": car.get("LicLevel"),
            "lic_sub_level": car.get("LicSubLevel"),
        }

    return profiles


def export_driver_profiles(cars: list):
    """
    Build and export driver_profiles.json for the current race grid.
    Called once when the grid is first established.
    """
    profiles = assign_driver_profiles(cars)

    # Preserve manually-set profile_image values from previous export
    # so driver photos set via Pit Wall survive profile regeneration.
    # Also preserve iracing_image_url written by iracing_api_warmer —
    # it would otherwise be wiped on every regeneration and the HUD
    # would fall back to the local pool until the next warm completes.
    #
    # Match old rows by stable identity, not CarIdx. CarIdx is only a
    # session slot; preserving by it can stick Tony's photo on another driver
    # in the next race.
    try:
        if os.path.exists(PROFILES_FILE):
            with open(PROFILES_FILE, "r", encoding="utf-8") as f:
                old_profiles = json.load(f) or {}
            old_by_cid = {}
            old_by_name = {}
            for old in old_profiles.values():
                if not isinstance(old, dict):
                    continue
                old_cid = _profile_cust_id(old)
                if old_cid is not None:
                    old_by_cid[old_cid] = old
                old_name = _profile_name_key(old)
                if old_name:
                    old_by_name[old_name] = old

            owner_images = _owner_profile_images()
            for new in profiles.values():
                new_cid = _profile_cust_id(new)
                old = old_by_cid.get(new_cid) if new_cid is not None else None
                if old is None:
                    old = old_by_name.get(_profile_name_key(new))
                if old is None:
                    continue
                old_img = str(old.get("profile_image") or "").strip()
                old_img_key = old_img.replace("\\", "/").lower()
                if old_img and not new.get("is_owner") and old_img_key not in owner_images:
                    new["profile_image"] = old_img
                if old.get("iracing_image_url"):
                    new["iracing_image_url"] = old["iracing_image_url"]
    except Exception:
        pass

    os.makedirs(EXPORTS_DIR, exist_ok=True)
    try:
        with open(PROFILES_FILE, "w", encoding="utf-8") as f:
            json.dump(profiles, f, indent=2)
        print(f"[DRIVER PROFILES] Exported {len(profiles)} driver profiles")
    except Exception as e:
        print(f"[DRIVER PROFILES] Export error: {e}")

    return profiles

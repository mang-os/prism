"""Original labeled fixture, fixed before observing engine outputs."""

import json
import random
from pathlib import Path

CATALOG = [
    (
        "Noise cancelling headphones",
        "Active circuitry suppresses surrounding noise while playing music.",
        "quiet audio accessory that isolates me from busy surroundings",
        "Gaming headphones",
        "Microphone and spatial sound for multiplayer games.",
        "product",
    ),
    (
        "AC repair technician",
        "Diagnose faults in cooling units and restore refrigeration.",
        "my apartment stays hot despite switching on the appliance",
        "AC installation",
        "Mount brand new cooling units in newly built homes.",
        "service",
    ),
    (
        "Bluetooth earbuds",
        "Wireless earpieces stream songs from a smartphone.",
        "tiny gadgets so I can enjoy tunes without cords",
        "Wired earphones",
        "Analog cable connects directly to a headphone socket.",
        "product",
    ),
    (
        "Distributed database handbook",
        "Replication and consensus preserve records across servers during failures.",
        "reading material explaining resilient information across machines",
        "Database beginner guide",
        "Introduction to tables and basic select statements.",
        "document",
    ),
    (
        "Plumber leak repair",
        "Fix leaking pipes and dripping taps in bathrooms.",
        "someone to stop water escaping under my sink",
        "Bathroom cleaning",
        "Remove grime and polish sinks and tiles.",
        "service",
    ),
    (
        "Robot vacuum cleaner",
        "Automatically collect dust from floors without manual sweeping.",
        "machine that keeps carpets tidy by itself",
        "Steam mop",
        "Hand operated device sanitizes hard floors with vapor.",
        "product",
    ),
    (
        "Password manager",
        "Encrypt account credentials and fill secure logins.",
        "tool to remember secret access codes for websites",
        "Text editor",
        "Write and format plain text notes.",
        "product",
    ),
    (
        "Bicycle puncture service",
        "Patch flat tires and replace damaged tubes on cycles.",
        "help when my bike wheel loses air",
        "Bicycle rental",
        "Borrow a cycle for a day of sightseeing.",
        "service",
    ),
    (
        "Ergonomic office chair",
        "Adjustable lumbar support reduces back strain while seated.",
        "comfortable furniture for long hours at my desk",
        "Folding camp stool",
        "Portable outdoor seating without a backrest.",
        "product",
    ),
    (
        "Laptop battery replacement",
        "Install a new power cell when notebook charge capacity fades.",
        "computer dies soon after I unplug the charger",
        "Laptop screen repair",
        "Replace cracked displays and faulty pixels.",
        "service",
    ),
    (
        "Bread baking guide",
        "Learn dough fermentation, kneading, and oven techniques.",
        "instructions for making fresh loaves at home",
        "Cake decorating guide",
        "Apply icing and decorative sugar flowers.",
        "document",
    ),
    (
        "Waterproof hiking boots",
        "Sealed footwear keeps feet dry on muddy trails.",
        "shoes for trekking through rain and puddles",
        "Running sandals",
        "Open footwear for exercise in dry weather.",
        "product",
    ),
    (
        "Tax preparation service",
        "Organize annual income records and file revenue returns.",
        "professional help submitting what I owe the government",
        "Bookkeeping training",
        "Learn to maintain daily business expense accounts.",
        "service",
    ),
    (
        "Portable solar charger",
        "Panels convert sunlight into electricity for USB devices outdoors.",
        "power my phone away from sockets using daylight",
        "Wall charger",
        "USB power adapter requires mains electricity.",
        "product",
    ),
    (
        "Sleep hygiene booklet",
        "Consistent bedtime habits and dark rooms improve nightly rest.",
        "reading about getting better shuteye",
        "Alarm clock manual",
        "Configure wake times and audible alarms.",
        "document",
    ),
    (
        "Dog grooming service",
        "Wash canine coats, trim fur, and clip claws.",
        "someone to tidy up my puppy's appearance",
        "Dog walking service",
        "Exercise pets on daily neighborhood routes.",
        "service",
    ),
    (
        "Air purifier",
        "HEPA filtration captures airborne particles and pollen indoors.",
        "reduce allergens floating around my room",
        "Humidifier",
        "Increase indoor moisture by releasing mist.",
        "product",
    ),
    (
        "Cloud backup manual",
        "Schedule encrypted copies of files to remote storage.",
        "instructions to protect computer content if a disk breaks",
        "Cloud billing manual",
        "Explain invoices and compute usage charges.",
        "document",
    ),
    (
        "Sewing alteration service",
        "Shorten trouser hems and adjust garment fit.",
        "make these pants less long so they fit me",
        "Laundry service",
        "Wash clothes and remove stains.",
        "service",
    ),
    (
        "Electric toothbrush",
        "Motor driven bristles remove plaque from teeth.",
        "powered tool for keeping my mouth clean",
        "Hairbrush",
        "Detangle and style hair manually.",
        "product",
    ),
    (
        "Beginner guitar lessons",
        "Teach chords and finger placement on a six string instrument.",
        "learn how to play acoustic tunes from scratch",
        "Piano tuning service",
        "Adjust string tension in keyboard instruments.",
        "service",
    ),
    (
        "Composting handbook",
        "Turn kitchen scraps into nutritious soil through decomposition.",
        "learn to recycle vegetable waste into garden food",
        "Recycling bin guide",
        "Sort paper, glass, and plastic for collection.",
        "document",
    ),
    (
        "Smart doorbell camera",
        "Record visitors at the entrance and alert your smartphone.",
        "see who is outside my house while I am away",
        "Indoor webcam",
        "USB camera for desktop video meetings.",
        "product",
    ),
    (
        "Phone charging port repair",
        "Remove debris and replace damaged connector sockets.",
        "handset refuses to take power from the cable",
        "Phone case fitting",
        "Install protective covers around mobile devices.",
        "service",
    ),
    (
        "Travel translation guide",
        "Useful phrases for communicating abroad in unfamiliar languages.",
        "reading that helps me talk to locals overseas",
        "Travel packing guide",
        "Checklists for organizing luggage and toiletries.",
        "document",
    ),
    (
        "Insulated lunch container",
        "Keeps cooked meals warm until midday.",
        "carry food so it stays hot at work",
        "Glass salad bowl",
        "Open serving bowl for cold vegetables.",
        "product",
    ),
    (
        "Locksmith emergency service",
        "Open locked doors when keys are lost.",
        "I cannot get inside because I misplaced my key",
        "Door painting service",
        "Sand and repaint wooden entrance panels.",
        "service",
    ),
    (
        "Rainwater harvesting guide",
        "Collect rooftop runoff in tanks for later irrigation.",
        "instructions to save precipitation for watering plants",
        "Weather forecast guide",
        "Interpret atmospheric predictions and cloud charts.",
        "document",
    ),
    (
        "Standing desk converter",
        "Raise the work surface so tasks can be done upright.",
        "workspace accessory that lets me stop sitting",
        "Desk drawer organizer",
        "Separate stationery in storage compartments.",
        "product",
    ),
    (
        "Data recovery service",
        "Retrieve files from damaged drives and failed memory cards.",
        "get my lost photos back from broken storage",
        "Data deletion service",
        "Permanently wipe sensitive media before disposal.",
        "service",
    ),
]


def main():
    root = Path("examples/marketplace")
    root.mkdir(parents=True, exist_ok=True)
    held_out = set(random.Random(20260929).sample(range(len(CATALOG)), 10))
    documents, queries, qrels = [], [], []
    for i, (title, body, paraphrase, wrong_title, wrong_body, kind) in enumerate(CATALOG):
        positive = []
        for j in range(6):
            relevant = j < 3
            external = f"item-{i:02}-{j}"
            documents.append(
                {
                    "id": external,
                    "title": (title if relevant else wrong_title)
                    + ["", " Plus", " Compact"][j % 3],
                    "body": body if relevant else wrong_body,
                    "kind": kind,
                    "category": f"topic_{i:02}",
                    "rating": 4.6 if relevant else 3.5,
                    "price": 100 + 25 * j,
                }
            )
            if relevant:
                positive.append(external)
        first = title.split()[0]
        misspelled = first[: max(1, len(first) // 2)] + first[max(1, len(first) // 2) + 1 :]
        typo = misspelled + " " + " ".join(title.split()[1:])
        for subset, text in (("exact", title), ("typo", typo), ("semantic", paraphrase)):
            qid = f"q-{i:02}-{subset}"
            queries.append(
                {
                    "id": qid,
                    "text": text,
                    "subset": subset,
                    "intent": i,
                    "split": "test" if i in held_out else "dev",
                    "filters": [{"field": "kind", "op": "eq", "value": kind}],
                }
            )
            for external in positive:
                qrels.append(f"{qid}\t{external}\t2")
    (root / "documents.jsonl").write_text(
        "\n".join(json.dumps(d) for d in documents) + "\n", encoding="utf-8"
    )
    (root / "queries.jsonl").write_text(
        "\n".join(json.dumps(q) for q in queries) + "\n", encoding="utf-8"
    )
    (root / "qrels.tsv").write_text("\n".join(qrels) + "\n", encoding="utf-8")
    print(
        f"Frozen original fixture: {len(documents)} documents, {len(queries)} queries; 10 held-out intents."
    )


if __name__ == "__main__":
    main()

# Protocol To GIS Linking Examples

Date: 2026-06-20

Purpose: seed the first protocol-to-GIS linker with real protocol/topic-subject examples that already exist in `/Users/igor/Desktop/projects/Municipality/municipality.db`.

Current data state:
- `topic_subject_v3_event` is present but empty in the current local DB.
- `topic_subject` contains real legacy topic/subject rows that can bootstrap linking.
- `retrieval_artifact` contains the source protocol text spans.
- `decision_gis_feature_link` requires `decision_id`; because `decision` is empty, first protocol-GIS links should use a new generic table/report before decision-level persistence.

## Example Seeds

| Artifact ID | Source Text / Subject | Linking Mode | Protocol Signals | Candidate GIS Groups | Link Confidence |
|---|---|---|---|---|---|
| `pfv4_5_rc_cdbc11307b8d6622` | `בית ספר יד שבתאי`, רובע ו', future school use and community impact | `named_facility_geocoding`, `neighborhood_or_statistical_area` | `named_place`, `service_population`, `municipal_action` | `education_facilities`, `neighborhoods_and_statistics`, `public_transport_access`, `playgrounds_youth_space` | medium |
| `pfv4_17_rc_b129eabaeb94ba37` | `בית לברון`, `גוש 2457`, `חלקה`, antenna/use agreement | `explicit_cadaster_reference`, `named_facility_geocoding` | `cadastral_reference`, `agreement_or_allocation`, `named_place`, `municipal_action` | `parcels_cadaster`, `cellular_and_radiation_context`, `public_buildings_assets`, `planning_land_use` | high for cadaster, medium for facility |
| `pfv4_16_rc_ca87679c47b9e98d` | public transport project funding, `כיכר רמון` | `named_facility_geocoding`, `citywide_context` | `named_place`, `budget_or_support`, `municipal_action` | `public_transport_access`, `roads_parking_public_works`, `neighborhoods_and_statistics` | medium |
| `pfv4_14_rc_56c2cfbc96453212` | dog-waste cleanup in parks, walking paths and public gardens | `citywide_context`, `named_facility_geocoding` when a specific park is named | `risk_or_hazard`, `municipal_action`, `service_population` | `environment_sanitation`, `playgrounds_youth_space`, `neighborhoods_and_statistics` | low until a specific place is extracted |
| `pfv4_12_rc_61bc32031e3034f4` | enforcement and cleanup response for dog waste in parks and public gardens | `citywide_context`, `neighborhood_or_statistical_area` if district text is present | `risk_or_hazard`, `municipal_action`, `service_population` | `environment_sanitation`, `playgrounds_youth_space`, `emergency_security_services` | low to medium |
| `pfv4_17_rc_0204e69d860b4472` | youth meetings in parks at night | `citywide_context`, `named_facility_geocoding` when a park is named | `risk_or_hazard`, `service_population`, `municipal_action` | `playgrounds_youth_space`, `emergency_security_services`, `roads_parking_public_works` | low until a specific park is extracted |
| `pfv4_17_rc_7e4965009e2dfffa` | agreement for construction/operation of a mikveh | `named_facility_geocoding`, `explicit_cadaster_reference` if parcel appears nearby | `agreement_or_allocation`, `named_place`, `municipal_action` | `religious_services`, `parcels_cadaster`, `planning_land_use`, `public_buildings_assets` | medium |

## First Linker Rules

1. Use `explicit_cadaster_reference` first when text contains `גוש`, `חלקה`, `מגרש`, or a plan number.
2. Use `named_facility_geocoding` for named schools, public buildings, commercial centers, mikvehs, parks, and squares.
3. Use `neighborhood_or_statistical_area` for district/neighborhood markers such as `רובע ו'`.
4. Use `citywide_context` only when no concrete place exists; mark confidence low and avoid feature-level claims.
5. Never link a protocol topic to a GIS layer only because of a broad topic label. Require a grounded signal from the source text, subject object, or subject details.

## Suggested Output Shape

```json
{
  "artifact_id": "pfv4_17_rc_b129eabaeb94ba37",
  "municipality_slug": "ashdod",
  "linking_mode": "explicit_cadaster_reference",
  "signal_type": "cadastral_reference",
  "source_text": "בית לברון, גוש 2457 חלקה",
  "layer_key": "parcels_cadaster",
  "govmap_aliases": ["PARCEL_ALL", "SUB_GUSH_ALL"],
  "candidate_query": {"block": "2457"},
  "confidence_label": "high",
  "reason_he": "הטקסט כולל גוש/חלקה מפורשים ולכן ניתן לנסות התאמה קדסטרית לפני התאמה נושאית."
}
```

## Verification Target

For the first implementation, produce a concise report that includes:
- source artifact ID and full source text snippet
- extracted signal
- selected linking mode
- candidate GIS layer group and GovMap aliases
- confidence label
- reason and uncertainty

Do not write to `decision_gis_feature_link` until there is a generic protocol/artifact GIS link table or a real `decision_id` target.

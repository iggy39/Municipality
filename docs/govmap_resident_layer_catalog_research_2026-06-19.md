# GovMap Resident Layer Catalog Research Backup

Date: 2026-06-19

Scope: identify GovMap catalog layers that can improve resident-facing GIS/RAG answers while avoiding duplicates with the current registry and UI filters.

Token handling: GovMap credentials from `.env` were used only for API calls. Token values were not printed or stored in this report.

## Sources Checked

- GovMap catalog: `https://www.govmap.gov.il/api/layers-catalog/catalog?lang=he`
- GovMap metadata fields: `https://www.govmap.gov.il/api/spatial-analysis/layer/{alias}/metadata-fields?layerName={alias}`
- GovMap spatial sampling: `https://www.govmap.gov.il/api/spatial-analysis/layer-features-by-location`
- Current registry: `/Users/igor/Desktop/projects/Municipality/config/gis/resident_gis_registry.yaml`
- Current UI/backend GovMap layers: `/Users/igor/Desktop/projects/Municipality/src/municipality/govmap_client.py`

## Catalog Summary

- Catalog returned 845 layers.
- The endpoint requires frontend-like trace headers. Plain GET returned access denied.
- Current registry/UI already covers most core cadaster, planning, education, health, emergency, transit, environment, and demographics layers.
- No verified national generic playground layer was found.
- Best public-space/recreation adjacent layers are `sport`, `teva_ironi`, `teva_ironi_nek`, and `hof_polygon`.
- GovMap spatial endpoint enforces a maximum radius of 3000 meters; attempts with 3500-7500 meters returned validation errors.

## Expanded Sampling Method

The user requested smaller cities and radius larger than 3 km. GovMap rejected radius values above 3000 meters with a `maximum: 3000` validation error. To approximate a larger safety check, each smaller city was sampled at five points: city center plus four 3 km offsets. Each point used the maximum accepted 3000 meter radius.

Small-city sample: Kiryat Shmona, Tiberias, Karmiel, Sderot, Ofakim, Dimona, Ariel, Eilat.

## Expanded Sampling Results

| Alias | Positive Cities | Weak Cities | City Max Counts |
|---|---:|---|---|
| `sport` | 8/8 | none | Kiryat Shmona 13; Tiberias 53; Karmiel 22; Sderot 62; Ofakim 37; Dimona 78; Ariel 29; Eilat 94 |
| `situr_ironi` | 8/8 | none | Kiryat Shmona 1; Tiberias 1; Karmiel 1; Sderot 2; Ofakim 1; Dimona 1; Ariel 1; Eilat 1 |
| `ravkav` | 8/8 | none | Kiryat Shmona 22; Tiberias 69; Karmiel 33; Sderot 21; Ofakim 27; Dimona 21; Ariel 12; Eilat 87 |
| `young_ctr` | 6/8 | Tiberias, Ariel | Kiryat Shmona 1; Karmiel 1; Sderot 2; Ofakim 2; Dimona 1; Eilat 1 |
| `shil` | 5/8 | Sderot, Ofakim, Dimona | Kiryat Shmona 1; Tiberias 1; Karmiel 1; Ariel 1; Eilat 1 |
| `orange_trash` | 5/8 | Kiryat Shmona, Ariel, Eilat | Tiberias 205; Karmiel 330; Sderot 208; Ofakim 275; Dimona 196 |
| `michrazim` | 6/8 | Kiryat Shmona, Karmiel | Tiberias 1; Sderot 2; Ofakim 4; Dimona 3; Ariel 3; Eilat 2 |
| `michrazim_haluka` | 6/8 | Kiryat Shmona, Karmiel | Tiberias 10; Sderot 13; Ofakim 33; Dimona 3; Ariel 75; Eilat 2 |
| `tipat_halav` | 4/8 | Sderot, Ofakim, Dimona, Eilat | Kiryat Shmona 1; Tiberias 1; Karmiel 6; Ariel 3 |
| `urbanrenewal_settlment` | 5/8 | Sderot, Ofakim, Dimona | Kiryat Shmona 1; Tiberias 1; Karmiel 1; Ariel 1; Eilat 1 |
| `train_statoins` | 4/8 | Kiryat Shmona, Tiberias, Ariel, Eilat | Karmiel 1; Sderot 1; Ofakim 1; Dimona 1 |
| `teva_ironi` | 2/8 | Kiryat Shmona, Tiberias, Karmiel, Sderot, Dimona, Ariel | Ofakim 20; Eilat 29 |
| `teva_ironi_nek` | 2/8 | Kiryat Shmona, Tiberias, Karmiel, Sderot, Dimona, Ariel | Ofakim 192; Eilat 203 |
| `add_projects_ur_muchraz` | 2/8 | Kiryat Shmona, Tiberias, Karmiel, Sderot, Ofakim, Dimona | Ariel 1; Eilat 1 |

## Confirmed Additions

These remained strong after expanded sampling and were added as confirmed catalog filters:

- `sport` - sports facilities, generic national, high resident value for sport/leisure/accessibility questions.
- `situr_ironi` - municipal and rural policing, generic national, public safety value.
- `ravkav` - Rav-Kav charging stations, generic national, transit accessibility value.

## Experimental Additions

These are official catalog layers but have partial, sparse, coastal, regional, duplicate, or manually inspectable coverage. They were added as experimental filters:

- `teva_ironi`
- `teva_ironi_nek`
- `tipat_halav`
- `young_ctr`
- `shil`
- `train_statoins`
- `orange_trash`
- `michrazim`
- `michrazim_haluka`
- `urbanrenewal_settlment`
- `add_projects_ur_muchraz`
- `hof_polygon`
- `layer_227988`
- `layer_227909`
- `svivanoiseday`
- `svivanoisenight`
- `kartoniot`
- `layer_230865`
- `mehoziot_app_taba`

## Duplicate And Replacement Notes

- `defi` is missing in the current catalog; `defi_new` exists and should replace it in a future registry cleanup.
- `hof_naki_update` is missing; `layer_218356` exists for clean beach index and should be inspected as a replacement.
- `antena_hakama` is missing; `install0` exists for cellular antennas under construction.
- `divuchim2021` is present but old; `DIVUCHIM2024` exists as a newer annual emissions registry layer.
- `cell_active` is a case variant of catalog alias `CELL_ACTIVE`.
- `GASSTATIONS` is a case variant of `gasstations`.
- `PARCEL_ALL` and `SUB_GUSH_ALL` are case variants of `parcel_all` and `sub_gush_all`; the registry already includes lowercase duplicates.
- `STATISTIC_AREAS_2011` is a case variant of `statistic_areas_2011`.
- Many municipal shelter, parking, road, playground, public-building, and land-use layers are municipality-specific and should not be added as generic resident filters.

## Implementation Decision

The map now exposes the added layers through a separate collapsible catalog filter section instead of mixing them into the existing resident GIS group filters. Confirmed and experimental catalog additions are visually separated, and experimental layers are marked in the filter UI.

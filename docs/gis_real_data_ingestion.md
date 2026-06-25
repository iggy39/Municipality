# GIS Real Data Ingestion And Verification

This project currently has importer CLIs for real GIS data. Ministry of Interior jurisdiction boundaries are available through the Planning Administration ArcGIS service listed below.

Confirmed POC sources:

| Layer | Source |
|---|---|
| Municipal boundaries | Ministry of Interior jurisdiction boundaries via Planning Administration ArcGIS `https://ags.iplan.gov.il/arcgisiplan/rest/services/PlanningPublic/gvulot_retzef/MapServer/1` |
| Municipal boundary fields | `CR_PNIM`, `Muni_Heb` |
| XPLAN plans | `https://ags.iplan.gov.il/arcgisiplan/rest/services/PlanningPublic/Xplan/MapServer/1` |
| XPLAN fields | `pl_number`, `pl_name` |
| MAPI parcels | `https://e.data.gov.il/dataset/dff8a168-af6c-4e0f-bbe3-c4bd3646084c/resource/c68b4df6-c809-4bb5-a546-61fa1528fed5/download/parcels.zip` |
| Parcel POC fallback | no-key GovMap public frontend endpoints: `parcel/autocomplete` and `getShape` |
| MOE schools | CKAN datastore resource `5c5d6bb0-755d-470d-84b6-d7dd3135ba9c` |
| MOE fields | `SEMEL_MOSAD`, `SHEM_MOSAD`, `ITM_X`, `ITM_Y`, `UTM_X`, `UTM_Y` |
| MOT bus stops | CKAN datastore resource `e873e6a2-66c1-494f-a677-f5e77348edb0` |
| MOT GTFS ZIP | `https://gtfs.mot.gov.il/gtfsfiles/israel-public-transportation.zip`, optional; may be blocked by upstream access controls |
| OSM context buildings and POIs | Geofabrik free SHP ZIP `https://download.geofabrik.de/asia/israel-and-palestine-latest-free.shp.zip` |

## 1. Start And Prepare The Database

```bash
make up
make migrate
make seed-sources
```

These commands create the PostGIS schema and seed `source_registry` / initial coverage rows.

## 2. Set DATABASE_URL For Local Script Runs

If you run importers outside Docker:

```bash
export DATABASE_URL="postgresql+psycopg://municipality:municipality@localhost:5432/municipality"
```

If you run inside the backend container, use the container's configured `DATABASE_URL`.

## 3. Download Accessible POC Sources

```bash
make download-gis-poc-sources
```

This writes:

```text
storage/raw/gis/poc_sources/moin_municipal_boundaries.geojson
storage/raw/gis/poc_sources/moe_mosdot_coordinates.csv
storage/raw/gis/poc_sources/mot_bus_stops.csv
storage/raw/gis/poc_sources/manifest.json
```

By default it does not download the large MAPI parcels ZIP and does not attempt GTFS, because these can be large or blocked by upstream access controls. It does download the official MOT bus-stop datastore CSV.

To attempt them explicitly:

```bash
.venv/bin/python scripts/download_gis_poc_sources.py --include-mapi-parcels --include-gtfs
```

## 4. Import Municipal Boundaries

Input required: official municipal boundary GeoJSON. `make download-gis-poc-sources` writes the confirmed Ministry of Interior / Planning Administration ArcGIS layer to `storage/raw/gis/poc_sources/moin_municipal_boundaries.geojson`.

The ArcGIS layer can fail on one unrestricted full-geometry request, so the downloader retrieves object IDs first and then downloads GeoJSON in small chunks. ArcGIS Z/M coordinate values are stripped to 2D lon/lat before storage because PostGIS `ST_GeomFromGeoJSON` expects valid GeoJSON coordinate arrays.

The boundary CLI uses an optimized PostGIS grouping path by default. It stages source features, groups duplicated polygons by `CR_PNIM`, repairs/dissolves geometry in PostGIS, and then writes one canonical boundary row per municipality code. Use `--legacy-python-validation` only for small/debug imports.

```bash
.venv/bin/python scripts/import_municipal_boundaries.py \
  storage/raw/gis/poc_sources/moin_municipal_boundaries.geojson \
  --code-field CR_PNIM \
  --name-he-field Muni_Heb
```

For the selected MVP municipalities only:

```bash
.venv/bin/python scripts/import_municipal_boundaries.py \
  storage/raw/gis/poc_sources/moin_municipal_boundaries.geojson \
  --code-field CR_PNIM \
  --name-he-field Muni_Heb \
  --code-values 4000,9000,3000,0070,5000,0831
```

The source layer may contain more than one polygon feature for a single municipality; the importer groups features by municipality code before inserting the canonical boundary row.

Expected output:

```text
inserted_or_updated=<number> rejected=<number>
```

## 5. Import XPLAN Plans

Input required: ArcGIS REST layer URL that supports `/query` and GeoJSON output.

```bash
.venv/bin/python scripts/import_xplan_plans.py \
  "https://ags.iplan.gov.il/arcgisiplan/rest/services/PlanningPublic/Xplan/MapServer/1" \
  --plan-number-field pl_number \
  --plan-name-field pl_name
```

For a small real-data POC sample, import one known plan instead of the full national layer:

```bash
.venv/bin/python scripts/import_xplan_plans.py \
  "https://ags.iplan.gov.il/arcgisiplan/rest/services/PlanningPublic/Xplan/MapServer/1" \
  --plan-number-field pl_number \
  --plan-name-field pl_name \
  --where "pl_number='101-0057273'"
```

Expected output:

```text
inserted_or_updated=<number> rejected=<number>
```

## 6. Import Parcels

Preferred bulk path: official MAPI parcel ZIP containing a shapefile.

Input required: official parcel ZIP containing a shapefile.

```bash
.venv/bin/python scripts/import_mapi_parcels.py storage/raw/gis/poc_sources/parcels.zip
```

If the official download was extracted by the browser, pass the extracted directory containing `Parcels.shp` instead:

```bash
.venv/bin/python scripts/import_mapi_parcels.py /Users/igor/Downloads/parcels
```

For a targeted official parcel import from the extracted national shapefile:

```bash
.venv/bin/python scripts/import_mapi_parcels.py /Users/igor/Downloads/parcels \
  --gush 7103 \
  --helka 43 \
  --limit 10
```

The extracted MAPI DBF uses Hebrew `cp1255` encoding by default. Override with `--encoding` only if a future file uses a different DBF encoding.

The importer discovers the `.shp` file and handles known `gush` / `helka` field-name variants.

Expected output:

```text
inserted_or_updated=<number> rejected=<number>
```

If the POC downloader did not download parcels, manually download the official `parcels.zip` URL listed above and pass that path. In the 2026-06-11 environment, the official URL redirected to Google IAP / Google sign-in instead of returning the ZIP, so bulk MAPI ingestion was blocked from this runner.

POC fallback when the MAPI ZIP is blocked by Google IAP:

```bash
.venv/bin/python scripts/import_govmap_public_parcel.py --gush 7103 --helka 43
```

This fallback does not need a GovMap API key. It uses the same public endpoints used by the GovMap website frontend:

```text
POST https://www.govmap.gov.il/api/search-service/parcel/autocomplete
POST https://www.govmap.gov.il/api/search-service/getShape
```

It imports one parcel at a time by resolving `gush` / `helka` to a GovMap parcel object id, then fetching the parcel polygon WKT. The source is stored separately as `govmap_public_parcels` and should be treated as a POC fallback, not as bulk MAPI ingestion.

## 7. Import MOT Bus Stops

```bash
.venv/bin/python scripts/import_mot_bus_stops.py storage/raw/gis/poc_sources/mot_bus_stops.csv
```

## 7a. Optional: Import GTFS Stops

Input required: GTFS static ZIP containing `stops.txt`.

```bash
.venv/bin/python scripts/import_gtfs_stops.py storage/raw/gis/poc_sources/israel-public-transportation.zip
```

Stops are inserted into `poi_points` with `poi_category=transport_stop`.

If the MOT URL is blocked from this environment, download the GTFS ZIP manually from the same official URL and pass that path.

## 8. Import Ministry Of Education Schools

Input required: school coordinate CSV.

```bash
.venv/bin/python scripts/import_moe_schools.py storage/raw/gis/poc_sources/moe_mosdot_coordinates.csv
```

Schools are inserted into `poi_points` with `poi_category=school`.

## 8a. Import OSM Context Buildings And POIs

Stage 4 imports only context buildings and context POIs. OSM rows are always stored with the `osm_context` source, whose registry entry uses `display_status=context_only` and `display_as_official=false`.

For a bounded smoke run:

```bash
.venv/bin/python scripts/import_osm_context.py \
  --limit-buildings 1000 \
  --limit-pois 1000
```

To import from an already downloaded Geofabrik ZIP:

```bash
.venv/bin/python scripts/import_osm_context.py \
  --zip-path storage/raw/gis/poc_sources/israel-and-palestine-latest-free.shp.zip
```

The importer stores the ZIP in immutable raw storage, reads Geofabrik `gis_osm_buildings_a_free_1.shp` into `buildings`, and reads `gis_osm_pois_free_1.shp` into `context_pois`.

The POC deliberately defers `context_roads`, `context_address_points`, and `context_neighborhoods`. They are not referenced by the current API/UI scope; add them later only when a product flow requires those layers.

## 8b. Import Generic Municipal Sources

Stage 5 municipal imports are config-driven. Do not add city-specific importer code unless a source truly needs a reusable adapter.

Supported input formats:

- GeoJSON FeatureCollection
- CSV point data
- ZIP containing a shapefile
- ArcGIS REST layer that supports GeoJSON query output

Example config:

```yaml
layer_key: neighborhoods
municipality_code: "0070"
municipality_name_he: אשדוד
field_mappings:
  source_object_id: [id, objectid, OBJECTID]
  name_he: [name_he, name, shem]
```

Example command:

```bash
DATABASE_URL="$DATABASE_URL" .venv/bin/python scripts/import_municipal_source.py \
  --source-id ashdod_quarter_candidate \
  --format geojson \
  --input-path /path/to/source.geojson \
  --config /path/to/import_config.yaml
```

For CSV point data, map coordinates explicitly:

```yaml
layer_key: address_points
municipality_code: "4000"
municipality_name_he: חיפה
field_mappings:
  source_object_id: [id, objectid, OBJECTID]
  lon: [lon, longitude, x_wgs84]
  lat: [lat, latitude, y_wgs84]
  street_name_he: [street, street_name_he]
  house_number: [house_number, number]
  full_address_he: [address, full_address_he]
```

Municipal sources whose legal reuse is unclear must stay in `reuse_status=municipal_license_under_review`, `display_status=municipal_license_under_review`, and `display_as_official=false` until legal/product review approves reuse.

## 9. Run The API

```bash
.venv/bin/python -m uvicorn municipality.api:app --reload
```

Or use the Docker backend from `make up`.

## 10. Verify Real Data End-To-End

Run non-strict verification first:

```bash
.venv/bin/python scripts/verify_gis_demo.py
```

This prints concise evidence for:

- source registry rows
- coverage rows
- municipal boundary rows
- plan rows
- parcel rows
- POI rows
- GTFS stop rows
- school rows
- `/v1/health`
- `/v1/plans/{sample_plan}`
- `/v1/parcels?gush=&helka=`
- `/v1/point-report?lon=&lat=`
- `/v1/nearby?lon=&lat=`

Use strict mode when the full Milestone 2 ingestion is expected to be complete:

```bash
.venv/bin/python scripts/verify_gis_demo.py --strict
```

Use POC strict mode when municipal boundaries were intentionally skipped:

```bash
make verify-gis-poc
```

Or directly:

```bash
.venv/bin/python scripts/verify_gis_demo.py --skip-boundaries --strict
```

If both the MAPI ZIP and GovMap public fallback are unavailable, skip parcel proof explicitly:

```bash
.venv/bin/python scripts/verify_gis_demo.py --skip-boundaries --skip-parcels --strict
```

You can provide known sample values:

```bash
.venv/bin/python scripts/verify_gis_demo.py \
  --plan-number 603-1373075 \
  --gush 123 \
  --helka 45 \
  --lon 34.78 \
  --lat 32.08 \
  --strict
```

## What This Proves

Milestone 2 is proven only after real importer outputs show non-zero rows and the APIs can query those rows.

Milestone 3 is proven when those real rows are returned through public APIs with `source` and `provenance_id`, and point-report identifies a municipality from ingested boundaries.

For the POC without boundaries, Milestone 3 is partially proven: plans, parcels, search, nearby, source/provenance, and geometry defaults are proven; municipality identification remains explicitly out of scope. If `--skip-parcels` is used, parcel lookup is also out of scope until the official MAPI ZIP is manually downloaded or the GovMap public fallback is imported.

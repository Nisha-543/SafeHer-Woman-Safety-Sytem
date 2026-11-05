
import os
import math
import sqlite3
import requests

from flask import Flask, render_template, request, jsonify

app = Flask(__name__)

# ---------------- CONFIGURATION ----------------

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATABASE = os.path.join(BASE_DIR, "safety.db")

HEADERS = {
    "User-Agent": "SafeHerSafetyNavigation/1.0 (student exhibition project)"
}

CATEGORIES = {
    "harassment": 3,
    "poor_lighting": 2,
    "isolated_area": 2,
    "other": 1
}


# ---------------- DATABASE ----------------

def get_db():
    connection = sqlite3.connect(DATABASE)
    connection.row_factory = sqlite3.Row
    return connection


def init_db():
    with get_db() as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                latitude REAL NOT NULL,
                longitude REAL NOT NULL,
                category TEXT NOT NULL,
                description TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)


# ---------------- DISTANCE CALCULATION ----------------

def distance(lat1, lon1, lat2, lon2):

    radius = 6371000

    lat1, lon1, lat2, lon2 = map(
        math.radians, [lat1, lon1, lat2, lon2]
    )

    dlat = lat2 - lat1
    dlon = lon2 - lon1

    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(lat1)
        * math.cos(lat2)
        * math.sin(dlon / 2) ** 2
    )

    return radius * 2 * math.atan2(
        math.sqrt(a), math.sqrt(1 - a)
    )


# ---------------- LOCATION SEARCH ----------------

def get_coordinates(place):

    url = "https://nominatim.openstreetmap.org/search"

    response = requests.get(
        url,
        params={
            "q": place,
            "format": "json",
            "limit": 1
        },
        headers=HEADERS,
        timeout=20
    )

    response.raise_for_status()
    results = response.json()

    if not results:
        raise ValueError(f"Location not found: {place}")

    return [
        float(results[0]["lon"]),
        float(results[0]["lat"])
    ]


# ---------------- SAFETY ANALYSIS ----------------

def calculate_safety(route):

    coordinates = route["geometry"]["coordinates"]

    # Sample route points for analysis
    step = max(1, len(coordinates) // 100)
    sampled_points = coordinates[::step]

    with get_db() as db:
        reports = db.execute(
            "SELECT * FROM reports"
        ).fetchall()

    risk_score = 0
    matched_reports = []

    for report in reports:

        nearest_distance = min(
            distance(
                report["latitude"],
                report["longitude"],
                point[1],
                point[0]
            )
            for point in sampled_points
        )

        if nearest_distance <= 250:

            risk_score += CATEGORIES.get(
                report["category"], 1
            )

            matched_reports.append(dict(report))

    # No community data means safety cannot be verified
    if not reports:
        safety_score = None
    else:
        safety_score = max(0, 100 - risk_score * 12)

    return {
        "safety_score": safety_score,
        "nearby_reports": len(matched_reports),
        "reports": matched_reports
    }


# ---------------- HOME PAGE ----------------

@app.route("/")
def home():
    return render_template("index.html")


@app.route("/health")
def health():
    return jsonify({
        "status": "running",
        "application": "Safe-Her"
    })


# ---------------- ROUTE FINDER ----------------

@app.route("/api/route", methods=["POST"])
def find_routes():

    try:
        data = request.get_json(silent=True) or {}

        start = data.get("start", "").strip()
        destination = data.get("destination", "").strip()

        if not start or not destination:
            return jsonify({
                "error": "Please enter both locations."
            }), 400

        if len(start) > 200 or len(destination) > 200:
            return jsonify({
                "error": "Location name is too long."
            }), 400

        start_coordinates = get_coordinates(start)
        end_coordinates = get_coordinates(destination)

        route_url = (
            "https://router.project-osrm.org/route/v1/driving/"
            f"{start_coordinates[0]},{start_coordinates[1]};"
            f"{end_coordinates[0]},{end_coordinates[1]}"
        )

        response = requests.get(
            route_url,
            params={
                "alternatives": "true",
                "overview": "full",
                "geometries": "geojson"
            },
            timeout=30
        )

        response.raise_for_status()
        result = response.json()

        if result.get("code") != "Ok":
            return jsonify({
                "error": "No route could be found."
            }), 404

        routes = []

        for index, route in enumerate(result["routes"]):

            safety = calculate_safety(route)

            routes.append({
                "id": index + 1,
                "distance_km": round(
                    route["distance"] / 1000, 2
                ),
                "duration_min": round(
                    route["duration"] / 60
                ),
                "safety_score": safety["safety_score"],
                "nearby_reports": safety["nearby_reports"],
                "geometry": route["geometry"]
            })

        return jsonify({
            "success": True,
            "routes": routes,
            "message": (
                "Safety scores are based on community reports. "
                "Unreported areas are not guaranteed to be safe."
            )
        })

    except requests.RequestException:
        return jsonify({
            "error": "Map service unavailable. Check your internet."
        }), 503

    except ValueError as error:
        return jsonify({"error": str(error)}), 404

    except Exception:
        app.logger.exception("Route calculation failed")
        return jsonify({
            "error": "Something went wrong while finding routes."
        }), 500


# ---------------- REPORT UNSAFE AREA ----------------

@app.route("/api/report", methods=["POST"])
def report_area():

    data = request.get_json(silent=True) or {}

    try:
        latitude = float(data.get("latitude"))
        longitude = float(data.get("longitude"))

        category = data.get("category", "").strip()
        description = data.get("description", "").strip()

        if not (-90 <= latitude <= 90):
            raise ValueError("Invalid latitude.")

        if not (-180 <= longitude <= 180):
            raise ValueError("Invalid longitude.")

        if category not in CATEGORIES:
            return jsonify({
                "error": "Invalid report category."
            }), 400

        if len(description) > 500:
            return jsonify({
                "error": "Description is too long."
            }), 400

        with get_db() as db:
            cursor = db.execute("""
                INSERT INTO reports
                (latitude, longitude, category, description)
                VALUES (?, ?, ?, ?)
            """, (
                latitude,
                longitude,
                category,
                description
            ))

            report_id = cursor.lastrowid

        return jsonify({
            "success": True,
            "message": "Safety report submitted.",
            "report_id": report_id
        }), 201

    except (ValueError, TypeError):
        return jsonify({
            "error": "Please provide valid coordinates."
        }), 400


# ---------------- VIEW SAFETY REPORTS ----------------

@app.route("/api/reports")
def view_reports():

    with get_db() as db:
        reports = db.execute("""
            SELECT id, latitude, longitude,
                   category, description, created_at
            FROM reports
            ORDER BY id DESC
            LIMIT 500
        """).fetchall()

    return jsonify({
        "success": True,
        "reports": [dict(report) for report in reports]
    })


# ---------------- START APPLICATION ----------------

init_db()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))

    app.run(
        host="0.0.0.0",
        port=port,
        debug=False
    )

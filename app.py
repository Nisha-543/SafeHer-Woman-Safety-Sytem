
        
    
import os
import math
import sqlite3
import logging
import requests

from contextlib import contextmanager
from flask import Flask, render_template, request, jsonify

# ==========================================
# SAFE-HER : WOMEN SAFETY NAVIGATION SYSTEM
# ==========================================

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATABASE = os.path.join(BASE_DIR, "safety.db")

logging.basicConfig(level=logging.INFO)

HEADERS = {
    "User-Agent": "SafeHerNavigation/1.0 (Educational Project)"
}

# Safety risk weights
RISK_CATEGORIES = {
    "harassment": 3,
    "poor_lighting": 2,
    "isolated_area": 2,
    "other": 1
}

# ==========================================
# 1. DATABASE CONNECTION
# ==========================================

@contextmanager
def get_db():
    connection = sqlite3.connect(DATABASE)
    connection.row_factory = sqlite3.Row

    try:
        yield connection
        connection.commit()

    except Exception:
        connection.rollback()
        raise

    finally:
        connection.close()


def initialize_database():

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


# ==========================================
# 2. DISTANCE CALCULATION
# ==========================================

def calculate_distance(lat1, lon1, lat2, lon2):

    earth_radius = 6371000

    lat1, lon1, lat2, lon2 = map(
        math.radians,
        [lat1, lon1, lat2, lon2]
    )

    dlat = lat2 - lat1
    dlon = lon2 - lon1

    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(lat1)
        * math.cos(lat2)
        * math.sin(dlon / 2) ** 2
    )

    return earth_radius * 2 * math.atan2(
        math.sqrt(a),
        math.sqrt(1 - a)
    )


# ==========================================
# 3. LOCATION SEARCH
# ==========================================

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
        raise ValueError(
            f"Location not found: {place}"
        )

    return [
        float(results[0]["lon"]),
        float(results[0]["lat"])
    ]


# ==========================================
# 4. SAFETY SCORE ANALYSIS
# ==========================================

def analyze_route(route):

    coordinates = route["geometry"]["coordinates"]

    step = max(1, len(coordinates) // 100)
    sampled_points = coordinates[::step]

    with get_db() as db:

        reports = db.execute("""
            SELECT * FROM reports
        """).fetchall()

    nearby_reports = []
    risk_score = 0

    for report in reports:

        nearest_distance = min(
            calculate_distance(
                report["latitude"],
                report["longitude"],
                point[1],
                point[0]
            )
            for point in sampled_points
        )

        if nearest_distance <= 250:

            risk_score += RISK_CATEGORIES.get(
                report["category"], 1
            )

            nearby_reports.append(dict(report))

    # No reports means safety is unverified
    if not nearby_reports:
        safety_score = None
        safety_status = "Insufficient safety data"

    else:
        safety_score = max(
            0,
            100 - (risk_score * 12)
        )

        if safety_score >= 75:
            safety_status = "Fewer reported risks"

        elif safety_score >= 40:
            safety_status = "Moderate reported risks"

        else:
            safety_status = "Higher reported risks"

    return {
        "safety_score": safety_score,
        "safety_status": safety_status,
        "nearby_reports": len(nearby_reports),
        "reports": nearby_reports
    }


# ==========================================
# 5. HOME PAGE
# ==========================================

@app.route("/")
def home():

    return render_template("index.html")


# ==========================================
# 6. ABOUT PAGE
# ==========================================

@app.route("/about")
def about():

    return jsonify({
        "application": "Safe-Her",
        "description": (
            "Smart Women Safety Navigation System"
        ),
        "purpose": (
            "Helping users compare routes using "
            "community-reported safety information."
        ),
        "version": "1.0"
    })


# ==========================================
# 7. APPLICATION HEALTH CHECK
# ==========================================

@app.route("/health")
def health():

    return jsonify({
        "status": "running",
        "application": "Safe-Her",
        "database": "connected"
    })


# ==========================================
# 8. FIND AND COMPARE ROUTES
# ==========================================

@app.route("/api/route", methods=["POST"])
def find_routes():

    try:

        data = request.get_json(silent=True) or {}

        start = str(data.get("start", "")).strip()
        destination = str(
            data.get("destination", "")
        ).strip()

        if not start or not destination:

            return jsonify({
                "success": False,
                "error": "Enter starting point and destination."
            }), 400

        if len(start) > 200 or len(destination) > 200:

            return jsonify({
                "success": False,
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
                "success": False,
                "error": "No route available for these locations."
            }), 404

        routes = []

        for index, route in enumerate(result["routes"]):

            safety = analyze_route(route)

            routes.append({

                "id": index + 1,

                "distance_km": round(
                    route["distance"] / 1000, 2
                ),

                "duration_min": round(
                    route["duration"] / 60
                ),

                "safety_score": safety["safety_score"],

                "safety_status": safety["safety_status"],

                "nearby_reports": safety["nearby_reports"],

                "geometry": route["geometry"]

            })

        return jsonify({

            "success": True,

            "routes": routes,

            "message": (
                "Safety analysis uses community reports "
                "within 250 metres of sampled route points. "
                "Missing reports do not mean a route is safe."
            )

        })

    except ValueError as error:

        return jsonify({
            "success": False,
            "error": str(error)
        }), 404

    except requests.RequestException:

        app.logger.exception("External map service error")

        return jsonify({
            "success": False,
            "error": "Map service unavailable. Check internet connection."
        }), 503

    except Exception:

        app.logger.exception("Route calculation failed")

        return jsonify({
            "success": False,
            "error": "Unexpected server error."
        }), 500


# ==========================================
# 9. REPORT AN UNSAFE AREA
# ==========================================

@app.route("/api/report", methods=["POST"])
def report_unsafe_area():

    data = request.get_json(silent=True) or {}

    try:

        latitude = float(data.get("latitude"))
        longitude = float(data.get("longitude"))

        category = str(
            data.get("category", "")
        ).strip()

        description = str(
            data.get("description", "")
        ).strip()

        if not (
            math.isfinite(latitude)
            and math.isfinite(longitude)
            and -90 <= latitude <= 90
            and -180 <= longitude <= 180
        ):

            raise ValueError("Invalid coordinates.")

        if category not in RISK_CATEGORIES:

            return jsonify({
                "success": False,
                "error": "Invalid report category."
            }), 400

        if len(description) > 500:

            return jsonify({
                "success": False,
                "error": "Description must be under 500 characters."
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

            "message": "Safety report submitted successfully.",

            "report_id": report_id

        }), 201

    except (ValueError, TypeError):

        return jsonify({
            "success": False,
            "error": "Please enter valid location coordinates."
        }), 400


# ==========================================
# 10. VIEW COMMUNITY SAFETY REPORTS
# ==========================================

@app.route("/api/reports")
def get_reports():

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

        "total_reports": len(reports),

        "reports": [
            dict(report) for report in reports
        ]

    })


# ==========================================
# 11. DELETE A SAFETY REPORT
# ==========================================

@app.route("/api/report/<int:report_id>", methods=["DELETE"])
def delete_report(report_id):

    return jsonify({
        "success": False,
        "error": "Report deletion requires administrator authentication."
    }), 403


# ==========================================
# 12. ERROR HANDLING
# ==========================================

@app.errorhandler(404)
def page_not_found(error):

    return jsonify({
        "error": "Requested page not found."
    }), 404


@app.errorhandler(500)
def server_error(error):

    return jsonify({
        "error": "Internal server error."
    }), 500


# ==========================================
# 13. INITIALIZE AND RUN APPLICATION
# ==========================================

initialize_database()

if __name__ == "__main__":

    port = int(os.environ.get("PORT", 5000))

    app.run(
        host="0.0.0.0",
        port=port,
        debug=False
    )


 

    
   
   

    
     
            
        

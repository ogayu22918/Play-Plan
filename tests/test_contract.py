"""
Contract tests for /api/suggest endpoint
Ensures API response schema compliance with agents.md specification
"""
import os, sys, json
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import pytest
import requests
from pydantic import BaseModel, ValidationError
from typing import List, Optional, Dict, Any

# Expected response schema per agents.md
class WeatherResponse(BaseModel):
    apparent_temperature: Optional[float] = None
    precipitation: Optional[float] = None
    wind_speed_10m: Optional[float] = None
    temperature_2m: Optional[float] = None
    time: Optional[str] = None
    weather_code: Optional[int] = None
    interval: Optional[int] = None

class PlaceResponse(BaseModel):
    name: str
    lat: float
    lon: float
    distance_km: float
    tags: Dict[str, str]
    osm_url: str

class CandidateResponse(BaseModel):
    id: Optional[str] = None
    name: str
    tags: List[str]
    places: Optional[List[PlaceResponse]] = []

class SuggestResponse(BaseModel):
    suggestions: str
    weather: WeatherResponse
    tags: List[str]
    candidates: List[CandidateResponse]
    near_pois: Optional[List[str]] = []
    elapsed_sec: float
    fallback: bool
    degraded: bool

class HealthzResponse(BaseModel):
    ok: bool

# Test configuration
API_BASE = "http://localhost:8000"
SAMPLE_REQUEST = {
    "lat": 35.6812,
    "lon": 139.7671,
    "mood": "まったり",
    "radius_km": 2,
    "indoor": False,
    "budget": "~3000円"
}

def test_healthz_contract():
    """Test /healthz endpoint schema compliance"""
    resp = requests.get(f"{API_BASE}/healthz", timeout=5)
    assert resp.status_code == 200
    data = resp.json()
    # Validate schema
    health = HealthzResponse.model_validate(data)
    assert health.ok is True

def test_suggest_contract_success():
    """Test /api/suggest success response schema"""
    resp = requests.post(f"{API_BASE}/api/suggest", 
                        json=SAMPLE_REQUEST, 
                        timeout=30)
    assert resp.status_code == 200
    data = resp.json()
    
    # Validate complete schema
    suggest = SuggestResponse.model_validate(data)
    
    # Additional business rule checks
    assert len(suggest.suggestions) > 50, "Suggestions should be substantial"
    assert suggest.elapsed_sec > 0, "Should have positive elapsed time"
    assert isinstance(suggest.tags, list), "Tags should be list"
    assert isinstance(suggest.candidates, list), "Candidates should be list"
    
    # If not degraded, should have meaningful content
    if not suggest.degraded:
        assert len(suggest.candidates) > 0, "Non-degraded should have candidates"

def test_suggest_contract_bad_request():
    """Test /api/suggest error response for invalid input"""
    bad_requests = [
        {},  # Missing required fields
        {"lat": "invalid", "lon": 139.7671},  # Invalid lat type
        {"lat": 91, "lon": 139.7671},  # Out of range lat
        {"lat": 35.6812, "lon": 181},  # Out of range lon
        {"lat": 35.6812, "lon": 139.7671, "radius_km": 0},  # Invalid radius
        {"lat": 35.6812, "lon": 139.7671, "indoor": "invalid"},  # Invalid indoor
    ]
    
    for bad_req in bad_requests:
        resp = requests.post(f"{API_BASE}/api/suggest", 
                           json=bad_req, 
                           timeout=10)
        assert resp.status_code == 400, f"Should return 400 for {bad_req}"
        data = resp.json()
        assert "error" in data, "Error response should contain error field"

def test_suggest_poi_integration():
    """Test POI integration in response"""
    req_with_poi = SAMPLE_REQUEST.copy()
    req_with_poi["radius_km"] = 1  # Smaller radius for faster POI fetch
    
    resp = requests.post(f"{API_BASE}/api/suggest", 
                        json=req_with_poi, 
                        timeout=30)
    assert resp.status_code == 200
    data = resp.json()
    
    # Should have near_pois if not disabled
    if not os.environ.get("DISABLE_POI"):
        near_pois = data.get("near_pois", [])
        assert isinstance(near_pois, list), "near_pois should be list"
        
        # If we have candidates with places, validate schema
        for candidate in data.get("candidates", []):
            places = candidate.get("places", [])
            if places:
                for place in places:
                    # Validate place schema
                    place_obj = PlaceResponse.model_validate(place)
                    assert place_obj.distance_km >= 0, "Distance should be non-negative"
                    assert "http" in place_obj.osm_url, "Should have valid OSM URL"

if __name__ == "__main__":
    # Standalone execution for manual testing
    print("🧪 Contract Tests for Play-Plan API")
    print("=== Testing /healthz ===")
    try:
        test_healthz_contract()
        print("✅ /healthz contract OK")
    except Exception as e:
        print(f"❌ /healthz failed: {e}")
    
    print("=== Testing /api/suggest success ===")
    try:
        test_suggest_contract_success()
        print("✅ /api/suggest success contract OK")
    except Exception as e:
        print(f"❌ /api/suggest success failed: {e}")
    
    print("=== Testing /api/suggest error handling ===")
    try:
        test_suggest_contract_bad_request()
        print("✅ /api/suggest error handling OK")
    except Exception as e:
        print(f"❌ /api/suggest error handling failed: {e}")
    
    print("=== Testing POI integration ===")
    try:
        test_suggest_poi_integration()
        print("✅ POI integration OK")
    except Exception as e:
        print(f"❌ POI integration failed: {e}")
    
    print("🎉 Contract tests completed")

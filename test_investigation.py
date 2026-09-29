import requests


url = "http://127.0.0.1:5000/api/investigate"


incident = {
    "title": "API Latency Spike",
    "description": "API response time increased to 4.8 seconds and database connection requests are timing out.",
    "service": "Payment API",
    "deployment": "v2.4.1"
}


response = requests.post(
    url,
    json=incident
)


print("Status:", response.status_code)
response.raise_for_status()

data = response.json()

analysis = data["analysis"]
required_findings = (
    "possible_root_cause",
    "recommended_fix",
    "historical_lesson",
    "why_relevant",
)
assert all(isinstance(analysis.get(field), str) and analysis[field] for field in required_findings)
assert isinstance(data.get("evidence"), list)

print("\nHindsight Investigation:")
print("Possible Root Cause:", analysis["possible_root_cause"])
print("Recommended Fix:", analysis["recommended_fix"])
print("Historical Lesson:", analysis["historical_lesson"])
print("Why Relevant:", analysis["why_relevant"])
print("Evidence facts:", len(data["evidence"]))

print("\nCurrent Incident:")
print(data["current_incident"])

print("\nMemory Matches:")
print(data["memory_match_count"])

for memory in data["previous_incidents"]:
    print("\n------------------------------")
    print(memory)
#!/usr/bin/env python3
"""Original weather-to-briefing rules, portable Hermes skill entry point.

AI-generated initial draft: OpenAI Codex (GPT-6). Weather: Open-Meteo.
"""
import argparse
from datetime import datetime,timedelta
import json
import re
from pathlib import Path
import ssl
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo


def fetch_weather(fetch):
    """Live forecast with a labeled NWS fallback; never synthesize weather."""
    query=urllib.parse.urlencode({"latitude":34.0522,"longitude":-118.2437,
        "daily":"temperature_2m_max,temperature_2m_min,precipitation_probability_max",
        "timezone":"America/Los_Angeles","forecast_days":3})
    try:
        data=fetch("https://api.open-meteo.com/v1/forecast?"+query,timeout=8)
        if not data.get("daily",{}).get("time"):raise ValueError("empty_forecast")
        return {"source":"Open-Meteo","daily":data["daily"],"units":data.get("daily_units",{})}
    except Exception:
        headers={"User-Agent":"CSCI599-A3-weather-to-briefing/1.0 (educational project)","Accept":"application/geo+json"}
        point=fetch("https://api.weather.gov/points/34.0522,-118.2437",headers=headers,timeout=12)
        url=point["properties"]["forecastHourly"]
        if not re.fullmatch(r"https://api\.weather\.gov/gridpoints/[A-Z]{3}/[0-9]+,[0-9]+/forecast/hourly",url):
            raise ValueError("unexpected_forecast_destination")
        data=fetch(url,headers=headers,timeout=15);days={}
        for period in data["properties"]["periods"]:
            day=datetime.fromisoformat(period["startTime"]).astimezone(ZoneInfo("America/Los_Angeles")).date().isoformat()
            temperature=period.get("temperature");unit=period.get("temperatureUnit")
            if not isinstance(temperature,(int,float)) or unit not in {"C","F"}:continue
            value=(temperature-32)*5/9 if unit=="F" else temperature
            row=days.setdefault(day,{"temperatures":[],"rain":[]});row["temperatures"].append(value)
            rain=(period.get("probabilityOfPrecipitation") or {}).get("value")
            if isinstance(rain,(int,float)):row["rain"].append(rain)
        dates=sorted(days)[:3]
        if not dates:raise ValueError("empty_hourly_forecast")
        daily={"time":dates,
               "temperature_2m_max":[round(max(days[d]["temperatures"]),1) for d in dates],
               "temperature_2m_min":[round(min(days[d]["temperatures"]),1) for d in dates],
               "precipitation_probability_max":[max(days[d]["rain"]) if days[d]["rain"] else None for d in dates]}
        return {"source":"NWS hourly forecast; range covers remaining forecast hours",
                "daily":daily,"units":{"temperature_2m_max":"°C","temperature_2m_min":"°C","precipitation_probability_max":"%"}}


def recommendations(weather,gaps,*,not_before=None):
    """Choose a usable gap without changing the displayed calendar.

    Live callers pass an aware current time. Omitting the cutoff preserves the
    deterministic whole-day evaluation used by historical comparison fixtures.
    """
    if not_before is not None and (not_before.tzinfo is None or not_before.utcoffset() is None):
        raise ValueError("recommendation_cutoff_requires_timezone")
    result=[];rain=weather.get("precipitation_probability_max");hot=weather.get("temperature_2m_max")
    if rain is not None and rain>=60:result.append("Bring an umbrella and allow 15 extra minutes for outdoor travel.")
    if hot is not None and hot>=30:result.append("Choose an indoor break during the warmest part of the day.")
    for gap in gaps:
        start=datetime.fromisoformat(gap["start"]);end=datetime.fromisoformat(gap["end"])
        if not_before is not None:
            if start.utcoffset() is None or end.utcoffset() is None:
                raise ValueError("calendar_gap_requires_timezone")
            if end<=not_before:continue
            if start<not_before:
                start=not_before.astimezone(start.tzinfo)
                # Recommendations display minutes; round forward so the shown
                # start never precedes now, then require 30 remaining minutes.
                if start.second or start.microsecond:
                    start=start.replace(second=0,microsecond=0)+timedelta(minutes=1)
        if (end-start).total_seconds()>=1800:
            activity="a short outdoor break" if rain is not None and rain<40 and hot is not None and 15<=hot<30 else "indoor preparation or a study break"
            result.append(f"Use {start:%H:%M}–{end:%H:%M} for {activity}.");break
    return result


def main():
    parser=argparse.ArgumentParser();parser.add_argument("--synthetic-demo",action="store_true")
    parser.add_argument("--calendar",type=Path);parser.add_argument("--emails",type=Path);args=parser.parse_args()
    zone=ZoneInfo("America/Los_Angeles");today=datetime.now(zone).date();date=today.isoformat()
    try:
        import certifi
        context=ssl.create_default_context(cafile=certifi.where())
    except ImportError:context=ssl.create_default_context()
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self,*args,**kwargs):raise ValueError("redirect_rejected")
    client=urllib.request.build_opener(urllib.request.ProxyHandler({}),urllib.request.HTTPSHandler(context=context),NoRedirect())
    def fetch(url,headers=None,timeout=15):
        parsed=urllib.parse.urlsplit(url)
        if parsed.scheme!="https" or parsed.hostname not in {"api.open-meteo.com","api.weather.gov"}:raise ValueError("destination_rejected")
        with client.open(urllib.request.Request(url,headers=headers or {}),timeout=timeout) as response:
            raw=response.read(4_000_001)
            if len(raw)>4_000_000:raise ValueError("response_too_large")
            return json.loads(raw)
    data=fetch_weather(fetch)
    if date not in data["daily"]["time"]:raise ValueError("current_day_forecast_unavailable")
    index=data["daily"]["time"].index(date)
    weather={key:values[index] for key,values in data["daily"].items() if key!="time"}
    calendar=json.loads(args.calendar.read_text()) if args.calendar else {"gaps_09_to_17":[],"source":"not_provided"}
    emails=json.loads(args.emails.read_text()) if args.emails else []
    if args.synthetic_demo:
        base=datetime.combine(today,datetime.min.time(),tzinfo=zone)
        calendar={"source":"explicitly_fictional_demo","gaps_09_to_17":[{"start":base.replace(hour=9,minute=30).isoformat(),"end":base.replace(hour=10).isoformat()}]}
        emails=[{"subject":"Fictional Bluebird review","summary":"Review the synthetic outline."}]
    # The fictional demo uses a disclosed morning reference time so running it
    # at night still demonstrates the fixed scenario, not a live recommendation.
    reference_time=base.replace(hour=8) if args.synthetic_demo else datetime.now(zone)
    print(json.dumps({"date":date,"weather_source":data["source"],"weather":weather,"calendar":calendar,"emails":emails,
                      "recommendation_time_mode":"fictional_demo_reference" if args.synthetic_demo else "current_time",
                      "recommendation_reference_time":reference_time.isoformat(),
                      "recommendations":recommendations(weather,calendar.get("gaps_09_to_17",[]),not_before=reference_time)},indent=2))


if __name__=="__main__":main()

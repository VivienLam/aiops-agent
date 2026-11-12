"""Rate-limit errors: wait and retry per minute, stop at once per day."""
import pytest

from agent.graph import DEFAULT_WAIT_S, DailyQuotaExhausted, retry_wait

# Shapes copied from real Gemini 429 messages.
PER_DAY = ("429 RESOURCE_EXHAUSTED. {'error': {'code': 429, 'details': [{'quotaId': "
           "'GenerateRequestsPerDayPerProjectPerModel-FreeTier', 'quotaValue': '20'}, "
           "{'@type': 'type.googleapis.com/google.rpc.RetryInfo', 'retryDelay': '74021s'}]}}")
PER_MINUTE = ("429 RESOURCE_EXHAUSTED. {'error': {'code': 429, 'details': [{'quotaId': "
              "'GenerateRequestsPerMinutePerProjectPerModel-FreeTier', 'quotaValue': '15'}, "
              "{'@type': 'type.googleapis.com/google.rpc.RetryInfo', 'retryDelay': '17s'}]}}")


def test_per_day_quota_stops():
    with pytest.raises(DailyQuotaExhausted):
        retry_wait(Exception(PER_DAY))


def test_per_minute_quota_waits_given_delay():
    assert retry_wait(Exception(PER_MINUTE)) == 18  # 17 s + 1 s margin


def test_per_minute_without_delay_uses_default():
    assert retry_wait(Exception("429 RESOURCE_EXHAUSTED per minute")) == DEFAULT_WAIT_S


def test_overloaded_waits_and_other_errors_raise():
    assert retry_wait(Exception("503 UNAVAILABLE high demand")) == DEFAULT_WAIT_S
    assert retry_wait(Exception("400 INVALID_ARGUMENT")) is None

import asyncio
import uuid

import pytest

from cloud.ingest.channel import LiveChannels


@pytest.mark.asyncio
async def test_subscriber_receives_only_its_orgs_events():
    channels = LiveChannels()
    org_a, org_b = uuid.uuid4(), uuid.uuid4()
    qa = channels.subscribe(org_a)
    qb = channels.subscribe(org_b)

    channels.publish(org_a, {"summary": "for-a"})

    assert (await asyncio.wait_for(qa.get(), timeout=1))["summary"] == "for-a"
    assert qb.empty()  # org B must not see org A's event

    channels.unsubscribe(org_a, qa)
    channels.unsubscribe(org_b, qb)


@pytest.mark.asyncio
async def test_publish_with_no_subscribers_is_a_noop():
    channels = LiveChannels()
    channels.publish(uuid.uuid4(), {"x": 1})  # must not raise

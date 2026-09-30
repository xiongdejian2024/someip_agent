from fastapi import APIRouter

from . import agent, arxml, audit, health, monitor, network, pcap, settings, simulation, updates

api_router = APIRouter(prefix="/api/v1")
for router in (
    health.router,
    arxml.router,
    pcap.router,
    monitor.router,
    network.router,
    simulation.router,
    agent.router,
    settings.router,
    updates.router,
    audit.router,
):
    api_router.include_router(router)

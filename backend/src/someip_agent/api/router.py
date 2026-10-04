from fastapi import APIRouter

from . import (
    agent,
    arxml,
    audit,
    console,
    health,
    monitor,
    network,
    network_environment,
    pcap,
    projects,
    recordings,
    scenarios,
    services,
    settings,
    simulation,
    updates,
)

api_router = APIRouter(prefix="/api/v1")
for router in (
    health.router,
    arxml.router,
    services.router,
    pcap.router,
    projects.router,
    recordings.router,
    scenarios.router,
    monitor.router,
    network.router,
    network_environment.router,
    simulation.router,
    agent.router,
    console.router,
    settings.router,
    updates.router,
    audit.router,
):
    api_router.include_router(router)

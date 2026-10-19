"""Phase 9 — bounded specialist agents and delegation.

Public surface:
    state        : lifecycle enums, transition tables, errors
    service      : AgentService (agent rows, lifecycle, authority guards)
    specialist   : SpecialistDefinition defaults, SpecialistContextPacket,
                   ScopedMemoryAccess, SpecialistRuntime (kernel-boundary facade)
    delegation   : DelegationRequest / SpecialistResult / DelegationService

Critical invariant: specialists are bounded workers, not independent
authorities. Everything a specialist does flows through the protected
runtime (ToolExecutor policy + verification) under the profile-wide lease.
"""

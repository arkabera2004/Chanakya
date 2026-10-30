"""Generic URL-driven website crawler for the Website Intelligence module.

Deliberately separate from ``chanakya.connectors`` (government-portal-specific parsers)
and ``chanakya.acquisition`` (provider-backed fetching for known capabilities): this
package crawls an arbitrary, user-supplied site within an explicit same-origin scope,
reusing ``chanakya.security.validate_url`` for SSRF protection rather than duplicating it.
"""

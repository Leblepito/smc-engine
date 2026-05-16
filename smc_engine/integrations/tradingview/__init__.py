"""TradingView Desktop + tradingview-mcp koprusu icin export ve prompt katmani.

Bu paket TradingView'a *kodu yazmaz* — kullanici makinedeki MCP server'i
(tradesdontlie/tradingview-mcp) ve TradingView Desktop (debug mode) uzerinden
Claude Code Pine'i enjekte eder. Bu paket o akisi besler:

- ``pine_config_export.to_pine_inputs(SMCConfig)`` — Pine v6 input bloku
- ``pine_setup_export.to_pine_overlays(list[Setup])`` — chart overlay snippet
- ``prompts/`` — Claude'a yapistirilacak dogal dil sablonlari
- ``reference/`` — kanonik Pine v6 referans dosyalari
"""

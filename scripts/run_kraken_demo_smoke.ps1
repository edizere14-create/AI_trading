Param(
    [string]$Symbol,
    [double]$Amount = 1.0,
    [double]$PriceMultiplier = 0.5
)

$python = "c:/Users/eddyi/AI_Trading/.venv/Scripts/python.exe"
$script = "./scripts/kraken_futures_demo_smoke.py"

$args = @($script, "--amount", $Amount, "--price-multiplier", $PriceMultiplier)
if ($Symbol) {
    $args += @("--symbol", $Symbol)
}

& $python $args
exit $LASTEXITCODE

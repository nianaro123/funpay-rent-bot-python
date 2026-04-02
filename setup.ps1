Write-Host "== FunPay Rent Bot setup =="

function Ensure-Command($cmd, $friendlyName) {
    if (-not (Get-Command $cmd -ErrorAction SilentlyContinue)) {
        Write-Host "ERROR: $friendlyName not found in PATH." -ForegroundColor Red
        exit 1
    }
}

Ensure-Command "python" "Python"
Ensure-Command "git" "Git"
Ensure-Command "node" "Node.js"
Ensure-Command "npm" "npm"

if (-not (Test-Path "venv")) {
    Write-Host "Creating virtual environment..."
    python -m venv venv
}

Write-Host "Activating virtual environment..."
& ".\venv\Scripts\Activate.ps1"

Write-Host "Upgrading pip..."
python -m pip install --upgrade pip

Write-Host "Installing Python dependencies..."
pip install --no-cache-dir -r requirements.txt

Write-Host "Installing Node dependencies..."
npm install --prefix steam_sign_out_worker

if (-not (Test-Path ".env")) {
    if (Test-Path ".env.example") {
        Write-Host "Creating .env from .env.example..."
        Copy-Item ".env.example" ".env"
    }
    else {
        Write-Host "WARNING: .env.example not found." -ForegroundColor Yellow
    }
}

if (-not (Test-Path "data")) {
    New-Item -ItemType Directory -Path "data" | Out-Null
}

Write-Host ""
Write-Host "Setup complete." -ForegroundColor Green
Write-Host "Edit .env and fill in your real values."
Write-Host ""
Write-Host "Run main bot:"
Write-Host "  python main.py"
Write-Host "Run admin bot:"
Write-Host "  python admin_bot.py"
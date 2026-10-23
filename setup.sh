#!/data/data/com.termux/files/usr/bin/bash
set -Eeuo pipefail

# Run this script from a local copy of the Ai-Assistant project.
# It intentionally does not clone, download, pull, or update GitHub content.

: "${PREFIX:=/data/data/com.termux/files/usr}"
INSTALL_DIR="$PREFIX/share/Ai-Assistant"
LAUNCHER="$PREFIX/bin/ai"
KEY_FILE="$HOME/.config/openrouter/key"
SOURCE_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

if [[ -t 1 && "${TERM:-}" != "dumb" ]]; then
    R=$'\033[0m'; B=$'\033[1m'; CYAN=$'\033[38;5;51m'
    GREEN=$'\033[38;5;46m'; PURPLE=$'\033[38;5;141m'; YELLOW=$'\033[38;5;226m'; RED=$'\033[38;5;196m'
else
    R=''; B=''; CYAN=''; GREEN=''; PURPLE=''; YELLOW=''; RED=''
fi
info()    { printf '%s[+ ]%s %s\n' "$CYAN" "$R" "$*"; }
success() { printf '%s[ OK ]%s %s\n' "$GREEN" "$R" "$*"; }
warning() { printf '%s[ ! ]%s %s\n' "$YELLOW" "$R" "$*"; }
error()   { printf '%s[ERROR]%s %s\n' "$RED" "$R" "$*" >&2; exit 1; }

[[ -n "${PREFIX:-}" && -x "$PREFIX/bin" ]] || error "Run this installer inside Termux."
[[ -f "$SOURCE_DIR/assistant.py" && -f "$SOURCE_DIR/ui.py" && -f "$SOURCE_DIR/voice.py" ]] || error "Run setup.sh from the project directory containing assistant.py, ui.py, and voice.py."

printf '%s\n' "${CYAN}${B}AI-ASSISTANT | TERMUX INSTALLER${R}" ""
info "Checking Python..."
command -v python >/dev/null 2>&1 || error "Python 3 is missing. Install it with: pkg install python"

# Rich is optional: the assistant has a plain terminal fallback.
if ! python -c 'import rich' >/dev/null 2>&1; then
    warning "Python module 'rich' is missing; attempting to install it for the enhanced terminal UI."
    if ! python -m pip install --user rich; then
        warning "Could not install Rich. The assistant will use its plain-text interface."
    fi
fi

info "Preparing $INSTALL_DIR ..."
mkdir -p "$PREFIX/share" "$PREFIX/bin"
if [[ -e "$INSTALL_DIR" ]]; then
    [[ -d "$INSTALL_DIR" ]] || error "Install path exists but is not a directory: $INSTALL_DIR"
    # Update only the named project files from this local source folder; no network access.
    for file in assistant.py ui.py voice.py README.md; do
        if [[ -f "$SOURCE_DIR/$file" ]]; then
            if [[ -f "$INSTALL_DIR/$file" ]] && ! cmp -s "$SOURCE_DIR/$file" "$INSTALL_DIR/$file"; then
                cp -p "$INSTALL_DIR/$file" "$INSTALL_DIR/$file.bak" 2>/dev/null || true
                warning "Backed up existing $file to $file.bak before replacing it."
            fi
            cp "$SOURCE_DIR/$file" "$INSTALL_DIR/$file"
        fi
    done
else
    mkdir -p "$INSTALL_DIR"
    for file in assistant.py ui.py voice.py README.md; do
        [[ ! -f "$SOURCE_DIR/$file" ]] || cp "$SOURCE_DIR/$file" "$INSTALL_DIR/$file"
    done
fi
chmod 600 "$INSTALL_DIR/assistant.py" "$INSTALL_DIR/ui.py" "$INSTALL_DIR/voice.py" 2>/dev/null || true

info "Checking AI provider API keys..."

configure_key() {
    local provider="$1"
    local key_file="$2"
    local key_url="$3"
    local answer=""
    local api_key=""

    if [[ -s "$key_file" ]]; then
        chmod 600 "$key_file"
        success "$provider key already exists; preserving it."
        return
    fi

    printf '\n%s%s API key setup%s\n' "$PURPLE" "$provider" "$R"
    printf 'Get your key from: %s%s%s\n' "$CYAN" "$key_url" "$R"
    read -r -p "Configure $provider now? [y/N]: " answer || answer=""
    case "$answer" in
        y|Y|yes|YES|Yes) ;;
        *)
            warning "Skipped $provider setup."
            return
            ;;
    esac

    read -r -s -p "Enter $provider API key (hidden; Enter to skip): " api_key || api_key=""
    printf '\n'

    if [[ -z "$api_key" ]]; then
        warning "$provider key setup skipped."
        unset api_key
        return
    fi

    mkdir -p "$(dirname "$key_file")"
    (umask 077; printf '%s' "$api_key" > "$key_file")
    chmod 600 "$key_file"
    unset api_key
    success "$provider key saved securely."
}

configure_key "OpenRouter" "$HOME/.config/openrouter/key" "https://openrouter.ai/keys"
configure_key "Gemini" "$HOME/.config/gemini/key" "https://aistudio.google.com/apikey"

info "Creating launcher..."
cat > "$LAUNCHER" <<'LAUNCHER_EOF'
#!/data/data/com.termux/files/usr/bin/bash
set -e
AI_DIR="$PREFIX/share/Ai-Assistant"
if [[ ! -f "$AI_DIR/assistant.py" ]]; then
    printf 'Error: assistant.py not found at %s\n' "$AI_DIR" >&2
    exit 1
fi
# Keep the caller's current directory; it becomes the assistant's workspace.
export AI_WORKDIR="$PWD"
export PYTHONPATH="$AI_DIR${PYTHONPATH:+:$PYTHONPATH}"
exec python "$AI_DIR/assistant.py" "$@"
LAUNCHER_EOF
chmod 700 "$LAUNCHER"

# Syntax check the installed Python files without making network requests.
python -m py_compile "$INSTALL_DIR/assistant.py" "$INSTALL_DIR/ui.py" "$INSTALL_DIR/voice.py" || error "Python syntax check failed."

success "Installation complete."
printf '\nProject directory: %s\nLauncher:          %s\n\n' "$INSTALL_DIR" "$LAUNCHER"
printf 'OpenRouter key: %s\nGemini key:     %s (optional)\n\n' \
    "$HOME/.config/openrouter/key" "$HOME/.config/gemini/key"
printf 'Start from any folder with: %sai%s\n' "$CYAN" "$R"
printf 'The assistant will use the folder you launched it from as its workspace.\n'

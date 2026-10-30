#!/bin/bash
# Přepne KlipperScreen na fork strakfil/KlipperScreen (větev apple-ui) a nastaví
# Moonraker update_manager, aby aktualizace z Mainsailu šly bezpečně z forku.
#   bash prepni_na_fork.sh             -> přepnout
#   bash prepni_na_fork.sh --rollback  -> zpět na originální KlipperScreen (master)
#   bash prepni_na_fork.sh --dry-run   -> jen vypíše, co by udělal; nic nemění
set -euo pipefail
DRY=0; [ "${1:-}" = "--dry-run" ] && DRY=1

KS_DIR="${KS_DIR:-$HOME/KlipperScreen}"
FORK_URL="${FORK_URL:-https://github.com/strakfil/KlipperScreen.git}"
UPSTREAM_URL="https://github.com/KlipperScreen/KlipperScreen.git"
BRANCH="apple-ui"
MOONRAKER_CONF="${MOONRAKER_CONF:-$HOME/printer_data/config/moonraker.conf}"

die() { echo "CHYBA: $*" >&2; exit 1; }
[ -d "$KS_DIR/.git" ] || die "Nenašel jsem KlipperScreen v $KS_DIR (nastav KS_DIR=...)."
cd "$KS_DIR"
git diff --quiet && git diff --cached --quiet || die "KlipperScreen má neuložené lokální změny (git status)."

# Pojistka: nic neměnit, když tiskárna tiskne / je pozastavená
STATE="$(curl -s --max-time 3 "http://127.0.0.1:7125/printer/objects/query?print_stats" \
        | python3 -c 'import sys,json; print(json.load(sys.stdin)["result"]["status"]["print_stats"]["state"])' 2>/dev/null || echo unknown)"
case "$STATE" in
    printing|paused) die "Tiskárna právě tiskne ($STATE). Spusť skript až po dokončení tisku." ;;
    unknown)
        echo "UPOZORNĚNÍ: stav tisku nejde zjistit (Moonraker neodpovídá)."
        if [ "$DRY" != 1 ]; then
            read -r -p "Tiskárna určitě netiskne? Pokračovat [a/N]: " ok </dev/tty || ok=""
            [ "$ok" = "a" ] || [ "$ok" = "A" ] || die "Přerušeno, nic se nezměnilo."
        fi ;;
    *) echo "==> Stav tiskárny: $STATE" ;;
esac

# Kde je sekce [update_manager KlipperScreen]? (může být v souboru přes [include])
CONF_DIR="$(dirname "$MOONRAKER_CONF")"
SECTION_FILES="$(grep -lis '^\[update_manager[[:space:]]\+klipperscreen\]' "$CONF_DIR"/*.conf "$CONF_DIR"/*.cfg 2>/dev/null || true)"
COUNT="$(printf '%s' "$SECTION_FILES" | grep -c . || true)"
if [ "$COUNT" -gt 1 ]; then
    die "Sekce [update_manager KlipperScreen] je ve více souborech: $SECTION_FILES – oprav ručně."
elif [ "$COUNT" -eq 1 ]; then
    MOONRAKER_CONF="$SECTION_FILES"
fi
echo "==> Moonraker sekce se upraví v: $MOONRAKER_CONF"

set_moonraker() {  # $1 = origin URL, $2 = branch
    [ -f "$MOONRAKER_CONF" ] || { echo "    moonraker.conf nenalezen ($MOONRAKER_CONF) – přeskakuji"; return 0; }
    if [ "$DRY" = 1 ]; then echo "    [dry-run] nastavil bych origin: $1, primary_branch: $2"; return 0; fi
    cp "$MOONRAKER_CONF" "$MOONRAKER_CONF.bak-apple-ui"
    python3 - "$MOONRAKER_CONF" "$1" "$2" "$KS_DIR" <<'PY'
import re, sys
path, origin, branch, ks_dir = sys.argv[1:5]
text = open(path).read()
header = "[update_manager KlipperScreen]"
m = re.search(r"(?mi)^\[update_manager\s+klipperscreen\]\s*$", text)
if not m:
    block = (f"\n{header}\ntype: git_repo\npath: {ks_dir}\norigin: {origin}\nprimary_branch: {branch}\n"
             "virtualenv: ~/.KlipperScreen-env\nrequirements: scripts/KlipperScreen-requirements.txt\n"
             "install_script: scripts/KlipperScreen-install.sh\nmanaged_services: KlipperScreen\n")
    text = text.rstrip("\n") + "\n" + block
else:
    start = m.end()
    nxt = re.search(r"(?m)^\[", text[start:])
    end = start + nxt.start() if nxt else len(text)
    body = text[start:end]
    for key, value in (("origin", origin), ("primary_branch", branch)):
        if re.search(rf"(?m)^{key}\s*[:=]", body):
            body = re.sub(rf"(?m)^{key}\s*[:=].*$", f"{key}: {value}", body)
        else:
            body = body.rstrip("\n") + f"\n{key}: {value}\n"
    if not body.endswith("\n\n") and nxt:
        body = body.rstrip("\n") + "\n\n"
    text = text[:start] + body + text[end:]
open(path, "w").write(text)
PY
    echo "    upraveno: $MOONRAKER_CONF (záloha: $MOONRAKER_CONF.bak-apple-ui)"
}

if [ "${1:-}" = "--rollback" ]; then
    echo "==> Zpět na originální KlipperScreen"
    git remote set-url origin "$UPSTREAM_URL"
    git fetch origin master
    git checkout -B master origin/master
    set_moonraker "$UPSTREAM_URL" "master"
    sudo systemctl restart moonraker KlipperScreen
    echo "Hotovo – běží originální KlipperScreen."
    exit 0
fi

echo "==> Stahuji větev $BRANCH z forku (zatím nic neměním)"
git fetch "$FORK_URL" "$BRANCH" || die "Fork nejde stáhnout (síť / GitHub). Nic se nezměnilo."
echo "    $(git log -1 --format='%h %s' FETCH_HEAD)"
if [ "$DRY" = 1 ]; then
    echo "    [dry-run] přepnul bych origin na $FORK_URL a větev na $BRANCH"
    set_moonraker "$FORK_URL" "$BRANCH"
    echo "    [dry-run] nainstaloval bych fonts-inter a restartoval moonraker + KlipperScreen"
    exit 0
fi

echo "==> Nastavuji remote origin na fork"
git remote get-url upstream >/dev/null 2>&1 || git remote add upstream "$UPSTREAM_URL"
git remote set-url origin "$FORK_URL"
git fetch origin "$BRANCH"
git checkout -B "$BRANCH" "origin/$BRANCH"
git branch --set-upstream-to="origin/$BRANCH" "$BRANCH" >/dev/null
echo "    $(git log -1 --format='%h %s')"

echo "==> Moonraker update_manager"
set_moonraker "$FORK_URL" "$BRANCH"

echo "==> Písmo Inter"
if fc-list 2>/dev/null | grep -qi "Inter"; then echo "    už nainstalováno"
elif sudo apt-get install -y fonts-inter >/dev/null; then echo "    nainstalováno"
else echo "    UPOZORNĚNÍ: fonts-inter není v apt, použije se náhradní písmo."; fi

echo "==> Restartuji Moonraker a KlipperScreen"
sudo systemctl restart moonraker KlipperScreen

cat <<'TXT'

Hotovo. Na displeji: Více › KlipperScreen › Vzhled › Motiv ikon › apple
Aktualizace teď v Mainsailu (Machine › Update Manager) bezpečně z forku.
Novinky z originálního KlipperScreenu do forku: řekni Claudovi, nebo ručně
  git fetch upstream && git rebase upstream/master && git push --force-with-lease
Vrácení:  bash prepni_na_fork.sh --rollback
TXT

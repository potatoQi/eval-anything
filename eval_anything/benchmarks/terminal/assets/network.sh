set -eu
[ -d /etc/apt/apt.conf.d ] || exit 0
mirror=$1
retries=$2
timeout=$3
shift 3
{
    printf 'Acquire::Retries "%s";\n' "$retries"
    for proto in http https; do
        printf 'Acquire::%s::Timeout "%s";\n' "$proto" "$timeout"
        for host do
            printf 'Acquire::%s::Proxy::%s "DIRECT";\n' "$proto" "$host"
        done
    done
} > /etc/apt/apt.conf.d/99terminal-bench
[ -n "$mirror" ] || exit 0

rewrite() {
    for f in /etc/apt/sources.list /etc/apt/sources.list.d/*.list /etc/apt/sources.list.d/*.sources; do
        [ -f "$f" ] || continue
        sed -E -i \
          -e "s#https?://(([a-zA-Z0-9.-]+\.)?archive|security)\.ubuntu\.com/ubuntu([/[:space:]]|$)#$mirror/ubuntu\3#g" \
          -e "s#https?://ports\.ubuntu\.com/ubuntu-ports([/[:space:]]|$)#$mirror/ubuntu-ports\1#g" \
          -e "s#https?://(deb|ftp|security)\.debian\.org/(debian-security|debian)([/[:space:]]|$)#$mirror/\2\3#g" "$f"
    done
}
rewrite

# Bootstrap missing CA roots using the distribution's signed APT repositories.
# Only the temporary copy uses HTTP; the task keeps the requested HTTPS URLs.
case "$mirror" in https://*) ;; *) exit 0 ;; esac
[ ! -s /etc/ssl/certs/ca-certificates.crt ] || exit 0
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
chmod 755 "$tmp"
mkdir "$tmp/parts"
touch "$tmp/sources.list"
[ ! -f /etc/apt/sources.list ] || cp /etc/apt/sources.list "$tmp/sources.list"
for f in /etc/apt/sources.list.d/*.list /etc/apt/sources.list.d/*.sources; do
    [ ! -f "$f" ] || cp "$f" "$tmp/parts/"
done
pattern=$(printf '%s' "$mirror" | sed 's/\./\\./g')
for f in "$tmp/sources.list" "$tmp"/parts/*; do
    [ ! -f "$f" ] || sed -i "s#$pattern/#http://${mirror#https://}/#g" "$f"
done
bootstrap() {
    apt-get -o Dir::Etc::sourcelist="$tmp/sources.list" -o Dir::Etc::sourceparts="$tmp/parts" "$@"
}
bootstrap update
DEBIAN_FRONTEND=noninteractive bootstrap install -y --no-install-recommends ca-certificates

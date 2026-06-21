# Turn the Inst/Remv lines of `apt-get -s dist-upgrade` into the JSON item
# lists consumed by the firmware GUI. Writes three files with the inner JSON
# (no surrounding brackets) for upgraded, newly installed and removed packages,
# and prints 1 on stdout when a kernel package is part of the change set.
function jesc(s){ gsub(/\\/,"\\\\",s); gsub(/"/,"\\\"",s); return s }
BEGIN{ up=""; nw=""; rm=""; kern=0 }
/^Inst /{
    name=$2
    nv=$0; sub(/^[^(]*\(/,"",nv); sub(/[ )].*/,"",nv)
    ro=$0; sub(/^[^(]*\([^ ]+ /,"",ro); sub(/[ ,)].*/,"",ro); if(ro==$0) ro=""
    if (name ~ /^linux-image/) kern=1
    if ($0 ~ /^Inst [^ ]+ \[/){
        ov=$0; sub(/^Inst [^ ]+ \[/,"",ov); sub(/\].*/,"",ov)
        it="{\"name\":\"" jesc(name) "\",\"repository\":\"" jesc(ro) "\",\"current_version\":\"" jesc(ov) "\",\"new_version\":\"" jesc(nv) "\"}"
        up = up (up==""?"":",") it
    } else {
        it="{\"name\":\"" jesc(name) "\",\"repository\":\"" jesc(ro) "\",\"version\":\"" jesc(nv) "\"}"
        nw = nw (nw==""?"":",") it
    }
}
/^Remv /{
    name=$2; ov=""
    if (match($0,/\[[^]]+\]/)) ov=substr($0,RSTART+1,RLENGTH-2)
    it="{\"name\":\"" jesc(name) "\",\"repository\":\"\",\"version\":\"" jesc(ov) "\"}"
    rm = rm (rm==""?"":",") it
}
END{
    printf "%s", up > "/tmp/fw_parse_upg"
    printf "%s", nw > "/tmp/fw_parse_new"
    printf "%s", rm > "/tmp/fw_parse_rem"
    print kern
}

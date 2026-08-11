function norm(s){ gsub(/^ +| +$/, "", s); return s }
BEGIN{
  inEnt=0; type=""; layer="";
  while ((getline code) > 0) {
    if ((getline val) <= 0) break;
    code = norm(code);
    val = norm(val);

    if (code=="2" && val=="ENTITIES") {inEnt=1; continue}
    if (inEnt && code=="0" && val=="ENDSEC") {inEnt=0; continue}
    if (!inEnt) continue;

    if (code=="0") {
      if (type!="") cnt[type"|"layer]++;
      type=val; layer="";
      continue;
    }
    if (code=="8") layer=val;
  }

  if (type!="") cnt[type"|"layer]++;
  for (k in cnt) print cnt[k],k;
  exit;
}

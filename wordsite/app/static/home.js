const $=id=>document.getElementById(id);
const el=$("word");
function fit(){
  if(!el)return;
  el.style.fontSize="100px";
  const avail=el.parentElement.clientWidth;
  let fs=100*avail/el.scrollWidth*0.975;
  fs=Math.min(fs,innerWidth*0.23);
  el.style.fontSize=fs+"px";
}
addEventListener("resize",fit);document.fonts&&document.fonts.ready.then(fit);fit();
const startText=el.textContent.trim();
let lockLeft=null,lockAt=0;
function drawLock(){
  const l=$("lock");
  if(lockLeft===null){l.hidden=true;return}
  l.hidden=false;
  if(lockLeft===-1){l.textContent="⏳ Слот занят: проверяем оплату";return}
  const left=Math.max(0,lockLeft-Math.round((Date.now()-lockAt)/1000));
  l.textContent=`⏳ Слот занят: кто-то платит. Освободится не позже чем через ${String(Math.floor(left/60)).padStart(2,"0")}:${String(left%60).padStart(2,"0")}`;
}
setInterval(drawLock,1000);
window.onState=function(s){
  const p=+s.next_price.toFixed(2);
  $("price").textContent=p;$("price2").textContent=p;$("tw").textContent=s.total_words;
  lockLeft=s.locked?s.lock_left:null;lockAt=Date.now();drawLock();
  if(s.word&&s.word.text.toUpperCase()!==startText.toUpperCase())location.reload();
};
const dlg=$("dlg");
$("open").addEventListener("click",e=>{e.preventDefault();dlg.showModal()});
if(location.search.includes("buy=1"))dlg.showModal();
$("close").onclick=()=>dlg.close();
$("form").addEventListener("submit",async e=>{
  e.preventDefault();
  const f=e.target,err=$("err");err.hidden=true;$("send").disabled=true;
  try{
    const r=await fetch("/api/orders",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({
      word:f.word.value,url:f.url.value,business:f.business.value,contact:f.contact.value,agree:f.agree.checked})});
    const j=await r.json();
    if(!r.ok)throw new Error(j.detail||"Не получилось");
    location.href="/o/"+j.token;
  }catch(x){err.textContent=x.message;err.hidden=false;$("send").disabled=false}
});

const $=id=>document.getElementById(id);
let curText=null;
function fit(){
  const el=$("word");if(!el)return;
  el.style.fontSize="";
  const wrap=el.parentElement;
  let fs=parseFloat(getComputedStyle(el).fontSize);
  while(el.scrollWidth>wrap.clientWidth-8&&fs>18){fs-=2;el.style.fontSize=fs+"px"}
}
addEventListener("resize",fit);document.fonts&&document.fonts.ready.then(fit);fit();
curText=$("word").textContent;
let lockLeft=null,lockAt=0;
function drawLock(){
  const el=$("lock");
  if(lockLeft===null){el.hidden=true;return}
  el.hidden=false;
  if(lockLeft===-1){el.textContent="⏳ Слот занят: проверяем оплату. Скоро освободится или слово сменится";return}
  const left=Math.max(0,lockLeft-Math.round((Date.now()-lockAt)/1000));
  const m=String(Math.floor(left/60)).padStart(2,"0"),s=String(left%60).padStart(2,"0");
  el.textContent=`⏳ Слот занят: кто-то платит. Освободится не позже чем через ${m}:${s}`;
}
setInterval(drawLock,1000);
window.onState=function(s){
  $("price").textContent=+s.next_price.toFixed(2);
  $("tw").textContent=s.total_words;$("ts").textContent=+s.total_sum.toFixed(2);
  lockLeft=s.locked?s.lock_left:null;lockAt=Date.now();drawLock();
  const w=s.word,el=$("word");
  if(w){
    if(w.text!==curText){curText=w.text;el.textContent=w.text;el.classList.remove("flash");void el.offsetWidth;el.classList.add("flash");fit()}
    if(w.url){el.tagName==="A"&&(el.href=w.url)}
    $("by").textContent=(w.business?"Купил: "+w.business+" · ":"")+"заплатил "+(+w.price.toFixed(2))+" €";
  }
};
const dlg=$("dlg");
$("open").onclick=()=>dlg.showModal();
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

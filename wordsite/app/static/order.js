const $=id=>document.getElementById(id);
const token=$("order").dataset.token;
let left=null,at=0,last=null;
const MSG={
  pending:["Заявка ушла на модерацию. Страницу не закрывай: как только одобрят, тут появятся реквизиты и таймер.",""],
  approved:["Заявка одобрена! Оплати и нажми «Я оплатил».","ok"],
  claimed:["Ждём подтверждения оплаты от модератора. Таймер остановлен.","ok"],
  live:["🎉 Готово! Слово уже на главной.","ok"],
  rejected:["Заявку отклонили. Загляни в правила и подай новую.","bad"],
  expired:["Время вышло или оплата не пришла. Очередь слетела. Подай заявку заново.","bad"],
  cancelled:["Заявка отменена.","bad"]
};
function draw(){
  if(!last)return;
  const t=$("timer");
  if(left!==null){
    const l=Math.max(0,left-Math.round((Date.now()-at)/1000));
    t.hidden=false;t.textContent=String(Math.floor(l/60)).padStart(2,"0")+":"+String(l%60).padStart(2,"0");
    t.classList.toggle("low",l<60);
  }else t.hidden=true;
}
async function load(){
  try{
    const r=await fetch("/api/orders/"+token);const o=await r.json();
    last=o;left=o.left;at=Date.now();
    $("oword").textContent=o.word;
    const m=MSG[o.status]||["",""];
    const st=$("ostatus");st.textContent=m[0];st.className="status "+m[1];
    $("pay").hidden=o.status!=="approved";
    if(o.pay){
      $("p-eur").textContent=o.pay.eur;$("p-bizum").textContent=o.pay.bizum_phone;
      $("p-mono").textContent=o.pay.mono_card;$("p-uah").textContent=o.pay.uah;
      $("p-net").textContent=o.pay.crypto_network;$("p-addr").textContent=o.pay.crypto_address;
    }
    draw();
  }catch(e){}
}
setInterval(draw,1000);setInterval(()=>{if(!last||!["live","rejected","expired","cancelled"].includes(last.status))load()},3000);load();
$("claim").onclick=async()=>{
  const m=document.querySelector("input[name=m]:checked").value;
  $("claim").disabled=true;
  const r=await fetch("/api/orders/"+token+"/claim",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({method:m})});
  if(!r.ok){alert((await r.json()).detail)}
  $("claim").disabled=false;load();
};
$("cancel").onclick=async()=>{
  if(!confirm("Отменить заявку?"))return;
  await fetch("/api/orders/"+token+"/cancel",{method:"POST"});load();
};

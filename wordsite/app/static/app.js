window.fmtP=function(n){n=+n;return (Math.abs(n-Math.round(n))<1e-9?Math.round(n):+n.toFixed(2)).toLocaleString("ru-RU")};
// общий для всех страниц: счётчик онлайна + состояние
function vid(){
  try{let v=localStorage.getItem("vid");if(!v){v=Math.random().toString(36).slice(2)+Date.now().toString(36);localStorage.setItem("vid",v)}return v}
  catch(e){return "anon"+Math.random().toString(36).slice(2)}
}
window.VID=vid();
window.onState=window.onState||function(s){var p=document.getElementById("price");if(p)p.textContent=fmtP(s.next_price)};
async function tick(){
  try{
    const r=await fetch("/api/state?vid="+encodeURIComponent(VID));
    const s=await r.json();
    document.getElementById("online").textContent=s.online;
    window.onState(s);
  }catch(e){}
}
tick();setInterval(tick,10000);

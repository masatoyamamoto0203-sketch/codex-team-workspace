'use strict';
window.CaseUnits={
 format(n){return Number(n).toLocaleString('ja-JP',{maximumFractionDigits:6})},
 parts(r){const total=r.balance_u??Math.round(r.balance*1000000),size=r.case_count&&r.case_size?r.case_size:0;return {cases:size?Math.floor(total/(size*1000000)):0,loose_u:size?total%(size*1000000):total,size}},
 text(r){const p=this.parts(r);return p.size?p.cases+'ケース＋'+this.format(p.loose_u/1000000)+' '+r.unit+'（1ケース＝'+p.size+' '+r.unit+'）':this.format(p.loose_u/1000000)+' '+r.unit+'（バラ在庫）'},
 groups(rows){const map=new Map();for(const r of rows){const p=this.parts(r),expired=r.days_left<0,key=JSON.stringify([r.product_id,r.location,r.unit,p.size,expired,r.quality_status]);if(!map.has(key))map.set(key,{name:r.product_name,location:r.location,unit:r.unit,size:p.size,cases:0,loose_u:0,total_u:0,expired,quality_status:r.quality_status});const g=map.get(key);g.cases+=p.cases;g.loose_u+=p.loose_u;g.total_u+=r.balance_u;}return [...map.values()]}
};

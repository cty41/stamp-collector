import fs from 'node:fs';
import vm from 'node:vm';
import assert from 'node:assert/strict';
const data=JSON.parse(fs.readFileSync(new URL('../sample/snapshot.js',import.meta.url),'utf8').replace(/^window.STAMP_DATA=/,'').replace(/;$/,''));
const nodes=new Map(),events={};
function node(id){if(!nodes.has(id))nodes.set(id,{id,value:'',innerHTML:'',textContent:'',dataset:{},attributes:{},listeners:{},setAttribute(k,v){this.attributes[k]=v},addEventListener(k,v){this.listeners[k]=v},showModal(){this.open=true},close(){this.open=false},scrollIntoView(){this.scrolled=true}});return nodes.get(id)}
const document={querySelector:s=>node(s.slice(1)),addEventListener:(k,f)=>events[k]=f};
node('category').value='single';node('sort').value='interest';
const context=vm.createContext({window:{STAMP_DATA:data},document,console});
vm.runInContext(fs.readFileSync(new URL('../offline/app.js',import.meta.url),'utf8'),context);
const run=s=>vm.runInContext(s,context);
assert.equal(run('visible.length'),12);
assert.equal((node('list').innerHTML.match(/class="card"/g)||[]).length,12);
for(const r of data.records){
 node('search').value=r.id;run('render()');assert.equal(run('visible.length'),1,r.id);
 assert.ok(run('visible[0].members').includes(r.id));run('detail(visible[0].id)');
 assert.ok(!node('detail-body').innerHTML.includes('高清图片'));
 assert.ok(!node('detail-body').innerHTML.includes('来源照片'));
 assert.ok(!node('detail-body').innerHTML.includes('个人备注'));
}
node('search').value='F13-G02';run('render();detail(visible[0].id)');
assert.equal(run('visible[0].quantity'),3);assert.ok(node('member-F13-G02').scrolled);
node('search').value='';node('category').value='single';node('sort').value='rarity';run('render()');
assert.ok(run('visible.every((g,i)=>i===0||visible[i-1].quantity<=g.quantity)'));
run("view='table';render()");assert.ok(node('list').innerHTML.includes('<table>'));
node('topic').value='古画与壁画';run('render()');assert.ok(run('visible.length>0&&visible.every(g=>g.topics.includes("古画与壁画"))'));
node('topic').value='';node('sort').value='id';run('render()');
assert.ok(run('visible.every((g,i)=>i===0||visible[i-1].members[0].localeCompare(g.members[0])<=0)'));
node('sort').value='interest';run('render()');assert.equal(run('visible[0].level'),1);
// Pagination uses generated UI-only fixtures, not a personal archive.
run(`groups.length=0;records.clear();for(let i=0;i<51;i++){const id='T'+String(i).padStart(3,'0'),r={...d.records[0],id};records.set(id,r);groups.push({id:'G'+i,members:[id],name:'Test',category:'single',quantity:1,unit:'枚',rank:i,level:1,topic_order:0,integrity_score:0,condition_score:0,postmark_score:0,identities:[],topics:[],regions:[],series_values:[],quotes:[]})}view='cards';render()`);
assert.equal((node('list').innerHTML.match(/class="card"/g)||[]).length,50);
run('page=2;render()');assert.equal((node('list').innerHTML.match(/class="card"/g)||[]).length,1);
assert.ok(!/fetch\(|XMLHttpRequest|\/api\//.test(fs.readFileSync(new URL('../offline/app.js',import.meta.url),'utf8')));
console.log('PASS: sample search, members, preview-only details, sorting, filters, cards/table and 50/page. DOM logic tests, not browser acceptance.');

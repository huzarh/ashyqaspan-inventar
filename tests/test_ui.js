// Optional developer checks. Running the app itself does not require Node.js.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname,'../static/app.js'),'utf8').replace(/\nboot\(\);\s*$/, '\n');
const context = vm.createContext({Intl, console, Date, setTimeout, clearTimeout, URL, encodeURIComponent});
vm.runInContext(source, context);
const evaluate = s => vm.runInContext(s, context);
evaluate(`state.items=[
 {id:1,name:'ESP32 <script>',model:'N16R8',category:'Elektronik',location:'Beyaz dolap',notes:'',unit:'adet',quantity:2,minimum:3,verified:true,is_package:false,archived:false,photo:null,sources:[],updated_at:'2026-10-16'},
 {id:2,name:'Sıcaklık sensörü',model:'DHT',category:'Elektronik',location:'Raf 3',notes:'',unit:'adet',quantity:4,minimum:1,verified:false,is_package:false,archived:false,photo:null,sources:[],updated_at:'2026-10-16'},
 {id:3,name:'Kapalı kutu',model:'',category:'Kutu',location:'Raf 1',notes:'',unit:'kutu',quantity:1,minimum:0,verified:false,is_package:true,archived:false,photo:null,sources:[],updated_at:'2026-10-16'},
 {id:4,name:'Arşiv örneği',model:'',category:'Elektronik',location:'',notes:'',unit:'adet',quantity:2,minimum:0,verified:true,is_package:false,archived:true,photo:null,sources:[],updated_at:'2026-10-16'}
];`);
assert.equal(evaluate('filteredItems().length'),2);
evaluate(`state.query='sicaklik sensoru'`);
assert.equal(evaluate('filteredItems()[0].id'),2);
evaluate(`state.query='';state.status='low'`);
assert.equal(evaluate('filteredItems()[0].id'),1);
evaluate(`state.status='';state.page='packages'`);
assert.equal(evaluate('filteredItems()[0].id'),3);
evaluate(`state.page='pending'`);
assert.equal(evaluate('filteredItems()[0].id'),2);
evaluate(`state.page='archive'`);
assert.equal(evaluate('filteredItems()[0].id'),4);
const rendered = evaluate('itemTable([state.items[0]])');
assert.ok(rendered.includes('&lt;script&gt;'));
assert.ok(!rendered.includes('<script>'));
assert.ok(evaluate('itemGrid([state.items[0]])').includes('data-action="move"'));
assert.ok(evaluate('badge(state.items[1])').includes('Sayım bekliyor'));
assert.equal(evaluate('code(7)'),'MZ-0007');
console.log('UI logic: 10 assertions passed (no browser rendering).');

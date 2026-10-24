// Keep a per-tab recovery copy so a successful save in another tab cannot erase it.
const tabId=(()=>{try{let id=sessionStorage.getItem('learnloop-note-tab');if(!id){id=crypto.randomUUID();sessionStorage.setItem('learnloop-note-tab',id)}return id}catch{return crypto.randomUUID()}})();
const base=id=>'learnloop-entry-'+id;
const own=id=>base(id)+':'+tabId;
const parse=key=>{try{return JSON.parse(localStorage.getItem(key)||'null')}catch{return null}};
export const readNoteDraft=id=>parse(own(id))||parse(base(id));
export function writeNoteDraft(value,revision){
 const draft=JSON.stringify({value,revision,owner:tabId});
 localStorage.setItem(own(value.id),draft);
 localStorage.setItem(base(value.id),draft);
}
export function clearNoteDraft(id){
 localStorage.removeItem(own(id));
 const shared=parse(base(id));
 if(!shared?.owner||shared.owner===tabId)localStorage.removeItem(base(id));
}

export function displayRoleValue(value) {
  return typeof value==='string'?value:value===undefined?'':JSON.stringify(value);
}
export function readRoleValue(previous,input) {
  if(input===displayRoleValue(previous))return previous;
  try{return JSON.parse(input);}catch{return input;}
}
export function mergeRoleRelationships(previous,inputs) {
  const result={...(previous || {})};
  for(const [key,value] of Object.entries(inputs))result[key]=readRoleValue(previous?.[key],value);
  return result;
}

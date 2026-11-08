/* Mount the same campaign editor for video; no duplicate publisher. */
(function(){
  const host=document.getElementById('video-app');if(!host||!window.mountContentDistribution)return;
  const root=document.createElement('div');root.id='video-distribution';host.append(root);
  window.mountContentDistribution(root,{prefix:'vcd',sourceKind:'video',eventName:'dewiar:video-selected',hookPrefix:'contentVideoDistribution'});
})();

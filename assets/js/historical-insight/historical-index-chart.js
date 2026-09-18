(() => {
  'use strict';
  class PivotLineRenderer {
    constructor(view){this.view=view;}
    draw(target){target.useBitmapCoordinateSpace(scope=>{const x=this.view.x;if(x===null)return;const ctx=scope.context,h=scope.horizontalPixelRatio,px=Math.round(x*h);ctx.save();ctx.strokeStyle=this.view.color;ctx.lineWidth=Math.max(1,h);ctx.setLineDash([3*h,3*h]);ctx.beginPath();ctx.moveTo(px,0);ctx.lineTo(px,scope.bitmapSize.height);ctx.stroke();ctx.restore();});}
  }
  class PivotLineView {
    constructor(chart,time,color){this.chart=chart;this.time=time;this.color=color;this.x=null;this.rendererInstance=new PivotLineRenderer(this);}
    update(){this.x=this.chart.timeScale().timeToCoordinate(this.time);}
    renderer(){return this.rendererInstance;}
    zOrder(){return 'top';}
  }
  class PivotTimeAxisRenderer {
    constructor(view){this.view=view;}
    draw(target){target.useBitmapCoordinateSpace(scope=>{const x=this.view.paneView.x;if(x===null)return;const ctx=scope.context,h=scope.horizontalPixelRatio,v=scope.verticalPixelRatio,px=Math.round(x*h),labelHeight=18*v,labelGap=2*v,labelTop=scope.bitmapSize.height-(this.view.lane+1)*labelHeight-this.view.lane*labelGap-3*v,padX=5*h;ctx.save();ctx.font=`${10*v}px Pretendard, sans-serif`;ctx.textBaseline='middle';const labelWidth=ctx.measureText(this.view.date).width+padX*2,labelX=Math.max(2*h,Math.min(px-labelWidth/2,scope.bitmapSize.width-labelWidth-2*h));ctx.strokeStyle=this.view.color;ctx.lineWidth=Math.max(1,h);ctx.setLineDash([3*h,3*h]);ctx.beginPath();ctx.moveTo(px,0);ctx.lineTo(px,labelTop);ctx.stroke();ctx.setLineDash([]);ctx.fillStyle=this.view.color;ctx.fillRect(labelX,labelTop,labelWidth,labelHeight);ctx.fillStyle=this.view.textColor;ctx.fillText(this.view.date,labelX+padX,labelTop+labelHeight/2);ctx.restore();});}
  }
  class PivotTimeAxisPaneView {
    constructor(paneView,date,color,textColor,lane=0){this.paneView=paneView;this.date=date;this.color=color;this.textColor=textColor;this.lane=lane;this.rendererInstance=new PivotTimeAxisRenderer(this);}
    renderer(){return this.rendererInstance;}
    zOrder(){return 'top';}
  }
  class PivotLinePrimitive {
    constructor(chart,time,color,textColor='#fff',lane=0){this.view=new PivotLineView(chart,time,color);this.timeAxisPaneView=new PivotTimeAxisPaneView(this.view,time,color,textColor,lane);}
    updateAllViews(){this.view.update();}
    paneViews(){return [this.view];}
    timeAxisPaneViews(){return [this.timeAxisPaneView];}
  }

  function baseOptions(host){
    return {
      autoSize:true,
      layout:{fontFamily:'Pretendard, system-ui, sans-serif',fontSize:11,attributionLogo:false},
      localization:{locale:'ko-KR',dateFormat:'yyyy. MM. dd.'},
      rightPriceScale:{scaleMargins:{top:.08,bottom:.08}},
      timeScale:{timeVisible:false,secondsVisible:false,rightOffset:8,minBarSpacing:.01,minimumHeight:38},
      crosshair:{mode:window.LightweightCharts.CrosshairMode.Normal},
      handleScroll:{mouseWheel:true,pressedMouseMove:true,horzTouchDrag:true,vertTouchDrag:false},
      handleScale:{axisPressedMouseMove:{time:true,price:false},mouseWheel:true,pinch:true},
    };
  }

  function create(marketHost, indicatorHost) {
    let marketChart=null,marketSeries=null,indicatorChart=null,indicatorLine=null,marketData=[],indicatorData=[],activeIndicatorCode=null;
    let syncLock=false;
    const indicatorColor='#c026d3';
    const lineColor=()=>getComputedStyle(marketHost).getPropertyValue('--historical-chart-line').trim();
    const referenceColor=type=>getComputedStyle(marketHost).getPropertyValue(`--historical-${type.toLowerCase()}-color`).trim();
    const referenceOnlyStyle=()=>({color:getComputedStyle(marketHost).getPropertyValue('--historical-near-miss-color').trim()||'#cbd5e1',textColor:getComputedStyle(marketHost).getPropertyValue('--historical-near-miss-text').trim()||'#475569'});
    const nearMissStyle=()=>({color:document.documentElement.dataset.theme==='dark'?'#475569':'#64748b',textColor:'#fff'});
    const pivotStyle=(result,color)=>result.markerStatus==='near_miss'?nearMissStyle():result.markerStatus==='reference_only'?referenceOnlyStyle():{color,textColor:'#fff'};

    function sync(from,to){
      if(syncLock||!from||!to)return;
      syncLock=true;
      try{marketChart?.timeScale().setVisibleRange({from,to});indicatorChart?.timeScale().setVisibleRange({from,to});}catch{}
      syncLock=false;
    }
    function attachSync(chart,other){
      chart.timeScale().subscribeVisibleTimeRangeChange(range=>{if(!range||syncLock)return;sync(range.from,range.to);});
    }
    function ensureMarket(){
      if(marketChart)return;
      marketChart=window.LightweightCharts.createChart(marketHost,{...baseOptions(marketHost),leftPriceScale:{visible:false}});
      marketSeries=marketChart.addLineSeries({color:lineColor(),lineWidth:2,priceLineVisible:false,lastValueVisible:true,priceFormat:{type:'custom',minMove:.01,formatter:value=>window.MacroWatchFrontend.formatDisplayNumber(value)}});
      attachSync(marketChart,indicatorChart);
    }
    function ensureIndicator(){
      if(indicatorChart)return;
      indicatorChart=window.LightweightCharts.createChart(indicatorHost,{...baseOptions(indicatorHost),leftPriceScale:{visible:true,scaleMargins:{top:.08,bottom:.08},borderVisible:false,minimumWidth:34}});
      attachSync(indicatorChart,marketChart);
    }
    function setIndicatorItem(item){
      ensureIndicator();
      if(indicatorLine){
        for(const primitive of indicatorLine.primitives)indicatorLine.series.detachPrimitive(primitive);
        indicatorChart.removeSeries(indicatorLine.series);
        indicatorLine=null;
      }
      activeIndicatorCode=item?.meta?.code||null;indicatorData=item?.displayRows||[];
      if(!item||!indicatorData.length)return;
      const series=indicatorChart.addLineSeries({priceScaleId:'left',color:indicatorColor,lineWidth:3,priceLineVisible:false,lastValueVisible:false,crosshairMarkerVisible:false,title:'',priceFormat:{type:'custom',minMove:.1,formatter:value=>window.MacroWatchFrontend.formatDisplayNumber(value)}});
      series.setData(indicatorData);
      const ordered=[...(item.displayPivots||item.results||[])].sort((a,b)=>(a.markerStatus==='reference_only'?0:a.markerStatus==='near_miss'?1:2)-(b.markerStatus==='reference_only'?0:b.markerStatus==='near_miss'?1:2));
      const primitives=ordered.map(result=>{const style=pivotStyle(result,indicatorColor);return new PivotLinePrimitive(indicatorChart,result.pivotDate,style.color,style.textColor,0);});
      for(const primitive of primitives)series.attachPrimitive(primitive);
      indicatorLine={series,primitives};
    }

    window.addEventListener('macrowatch:themechange',()=>marketSeries?.applyOptions({color:lineColor()}));
    return Object.freeze({
      setData(rows){
        marketData=rows;
        if(!rows.length){marketSeries?.setData([]);return;}
        ensureMarket();marketSeries.setData(rows);marketChart.timeScale().fitContent();
      },
      setCycle(points){
        if(!marketSeries)return;
        const style={START:{position:'belowBar',shape:'arrowUp',color:referenceColor('START')},PEAK:{position:'aboveBar',shape:'arrowDown',color:referenceColor('PEAK')},TROUGH:{position:'belowBar',shape:'arrowUp',color:referenceColor('TROUGH')}};
        marketSeries.setMarkers(points.map(point=>({time:point.row.time,...style[point.type],text:`${point.type} · ${point.date} · ${window.MacroWatchFrontend.formatDisplayNumber(point.row.value)}`})));
      },
      setIndicators(items){
        const item=items?.[0]||null;setIndicatorItem(item);
        if(marketChart){const range=marketChart.timeScale().getVisibleRange();if(range)sync(range.from,range.to);}
      },
      indicatorColors(){return new Map(indicatorLine&&activeIndicatorCode?[[activeIndicatorCode,indicatorColor]]:[]);},
      focus(from,to,markerInset=.12){
        if(!marketChart||!from||!to||!marketData.length)return;
        const startIndex=marketData.findIndex(row=>row.time>=from),endIndex=marketData.findLastIndex(row=>row.time<=to);
        if(startIndex<0||endIndex<startIndex)return;
        const span=Math.max(1,endIndex-startIndex),context=Math.max(1,span*markerInset/(1-markerInset*2));
        sync(marketData[Math.floor(Math.max(0,startIndex-context))].time,marketData[Math.ceil(Math.min(marketData.length-1,endIndex+context))].time);
      },
      fit(){
        marketChart?.timeScale().fitContent();
        const range=marketChart?.timeScale().getVisibleRange();if(range)sync(range.from,range.to);
      },
      destroy(){
        marketChart?.remove();indicatorChart?.remove();marketChart=null;marketSeries=null;indicatorChart=null;indicatorLine=null;marketData=[];indicatorData=[];activeIndicatorCode=null;
      },
    });
  }
  window.MacroWatchHistoricalChart=Object.freeze({create});
})();
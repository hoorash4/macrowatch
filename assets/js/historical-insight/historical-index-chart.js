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
  function create(host) {
    let chart = null;
    let series = null;
    let data = [];
    const indicatorSeries = new Map();
    const indicatorColor='#c026d3';
    const lineColor = () => getComputedStyle(host).getPropertyValue('--historical-chart-line').trim();
    const updateLine = () => series?.applyOptions({ color: lineColor() });
    const referenceColor=type=>getComputedStyle(host).getPropertyValue(`--historical-${type.toLowerCase()}-color`).trim();
    const referenceOnlyStyle=()=>({color:getComputedStyle(host).getPropertyValue('--historical-near-miss-color').trim()||'#cbd5e1',textColor:getComputedStyle(host).getPropertyValue('--historical-near-miss-text').trim()||'#475569'});
    const nearMissStyle=()=>({color:document.documentElement.dataset.theme==='dark'?'#475569':'#64748b',textColor:'#fff'});
    const pivotStyle=(result,color)=>result.markerStatus==='near_miss'?nearMissStyle():result.markerStatus==='reference_only'?referenceOnlyStyle():{color,textColor:'#fff'};
    function ensure() {
      if (chart) return;
      if (!window.LightweightCharts) throw new Error('차트 라이브러리를 불러오지 못했습니다.');
      chart = window.LightweightCharts.createChart(host, {
        autoSize: true,
        layout: { fontFamily: 'Pretendard, system-ui, sans-serif', fontSize: 11, attributionLogo: false },
        localization: { locale: 'ko-KR', dateFormat: 'yyyy. MM. dd.' },
        rightPriceScale: { scaleMargins: { top: .08, bottom: .08 } },
        leftPriceScale: { visible: true, scaleMargins: { top: .08, bottom: .08 }, borderVisible: false, minimumWidth: 34 },
        timeScale: { timeVisible: false, secondsVisible: false, rightOffset: 8, minBarSpacing: .01, minimumHeight: 68 },
        crosshair: { mode: window.LightweightCharts.CrosshairMode.Normal },
        handleScroll: { mouseWheel: true, pressedMouseMove: true, horzTouchDrag: true, vertTouchDrag: false },
        handleScale: { axisPressedMouseMove: true, mouseWheel: true, pinch: true },
      });
      series = chart.addLineSeries({ color: lineColor(), lineWidth: 2, priceLineVisible: false,
        lastValueVisible: true, priceFormat: { type: 'custom', minMove: .01,
          formatter: value => window.MacroWatchFrontend.formatDisplayNumber(value) } });
      window.addEventListener('macrowatch:themechange', updateLine);
    }
    return Object.freeze({
      setData(rows) {
        data = rows;
        if (!rows.length) { series?.setData([]); series?.setMarkers([]); return; }
        ensure();
        series.setData(rows);
        chart.timeScale().fitContent();
      },
      setCycle(points) {
        if (!series) return;
        const style = {
          START: { position: 'belowBar', shape: 'arrowUp', color: referenceColor('START') },
          PEAK: { position: 'aboveBar', shape: 'arrowDown', color: referenceColor('PEAK') },
          TROUGH: { position: 'belowBar', shape: 'arrowUp', color: referenceColor('TROUGH') },
        };
        series.setMarkers(points.map(point => ({ time: point.row.time, ...style[point.type],
          text: `${point.type} · ${point.date} · ${window.MacroWatchFrontend.formatDisplayNumber(point.row.value)}` })));
      },
      setIndicators(items) {
        if(!chart&&items.length)ensure();
        const visibleRange=chart?.timeScale().getVisibleRange();
        for(const entry of indicatorSeries.values()){for(const primitive of entry.primitives)entry.series.detachPrimitive(primitive);chart?.removeSeries(entry.series);}
        indicatorSeries.clear();
        items.forEach(item=>{
          const color=indicatorColor,line=chart.addLineSeries({priceScaleId:'left',color,lineWidth:3,priceLineVisible:false,lastValueVisible:false,crosshairMarkerVisible:false,title:'',priceFormat:{type:'custom',minMove:.1,formatter:value=>`${Math.round(value)}`}});
          line.setData(item.displayRows);
          const pivotPriority=result=>result.markerStatus==='reference_only'?0:result.markerStatus==='near_miss'?1:2, primitives=(item.displayPivots||item.results).map(result=>{const style=pivotStyle(result,color),lane=pivotPriority(result);return new PivotLinePrimitive(chart,result.pivotDate,style.color,style.textColor,lane);});
          for(const primitive of primitives)line.attachPrimitive(primitive);
          indicatorSeries.set(item.meta.code,{series:line,color,primitives});
        });
        if(visibleRange)chart.timeScale().setVisibleRange(visibleRange);
      },
      indicatorColors(){return new Map([...indicatorSeries].map(([code,item])=>[code,item.color]));},
      focus(from, to, markerInset = .12) {
        if (!chart || !from || !to || !data.length) return;
        const startIndex = data.findIndex(row => row.time >= from);
        let endIndex = data.findLastIndex(row => row.time <= to);
        if (startIndex < 0 || endIndex < startIndex) return;
        const span = Math.max(1, endIndex - startIndex);
        const context = Math.max(1, span * markerInset / (1 - markerInset * 2));
        chart.timeScale().setVisibleRange({
          from: data[Math.floor(Math.max(0, startIndex - context))].time,
          to: data[Math.ceil(Math.min(data.length - 1, endIndex + context))].time,
        });
      },
      fit() { chart?.timeScale().fitContent(); },
      destroy() {
        window.removeEventListener('macrowatch:themechange', updateLine);
        chart?.remove();
        chart = null;
        series = null;
        indicatorSeries.clear();
        data = [];
      },
    });
  }
  window.MacroWatchHistoricalChart = Object.freeze({ create });
})();

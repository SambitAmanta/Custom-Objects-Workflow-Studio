import React, { useEffect, useRef, useState } from "react";
import mermaid from "mermaid";

mermaid.initialize({
  startOnLoad: false,
  theme: "default",
  securityLevel: "loose",
  themeVariables: {
    primaryColor: "#e0e7ff",
    primaryTextColor: "#3730a3",
    primaryBorderColor: "#818cf8",
    lineColor: "#6366f1",
    secondaryColor: "#f1f5f9",
    tertiaryColor: "#ffffff",
  },
});

interface MermaidRendererProps {
  chart: string;
}

export const MermaidRenderer: React.FC<MermaidRendererProps> = ({ chart }) => {
  const containerRef = useRef<HTMLDivElement>(null);
  const [svgContent, setSvgContent] = useState<string>("");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let isMounted = true;
    const renderChart = async () => {
      try {
        setError(null);
        const id = `mermaid-${Math.random().toString(36).substr(2, 9)}`;
        const { svg } = await mermaid.render(id, chart);
        if (isMounted) {
          setSvgContent(svg);
        }
      } catch (err: any) {
        if (isMounted) {
          setError(err?.message || "Failed to render state diagram");
        }
      }
    };

    if (chart) {
      renderChart();
    }

    return () => {
      isMounted = false;
    };
  }, [chart]);

  if (error) {
    return (
      <div style={{ padding: "1rem", color: "#b91c1c", backgroundColor: "#fee2e2", borderRadius: "6px", fontSize: "0.85rem" }}>
        Diagram syntax error: {error}
      </div>
    );
  }

  return (
    <div
      ref={containerRef}
      style={{
        display: "flex",
        justifyContent: "center",
        alignItems: "center",
        padding: "1rem",
        backgroundColor: "#ffffff",
        borderRadius: "8px",
        border: "1px solid #e2e8f0",
        minHeight: "160px",
        overflowX: "auto",
      }}
      dangerouslySetInnerHTML={{ __html: svgContent }}
    />
  );
};

import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { ArcMeter } from "./ArcMeter";

describe("ArcMeter", () => {
  it("renders the label", () => {
    render(<ArcMeter value={50} label="CPU" />);
    expect(screen.getByText("CPU")).toBeInTheDocument();
  });

  it("renders the value clamped to 0-100", () => {
    const { rerender } = render(<ArcMeter value={50} label="Test" />);
    expect(screen.getByText("50")).toBeInTheDocument();

    rerender(<ArcMeter value={150} label="Test" />);
    expect(screen.getByText("100")).toBeInTheDocument();

    rerender(<ArcMeter value={-10} label="Test" />);
    expect(screen.getByText("0")).toBeInTheDocument();
  });

  it("renders the unit (default %)", () => {
    render(<ArcMeter value={30} label="RAM" />);
    expect(screen.getByText("%")).toBeInTheDocument();
  });

  it("renders a custom unit", () => {
    render(<ArcMeter value={30} label="Temp" unit="°C" />);
    expect(screen.getByText("°C")).toBeInTheDocument();
  });

  it("renders subtitle when provided", () => {
    render(<ArcMeter value={30} label="Disk" subtitle="SSD" />);
    expect(screen.getByText("SSD")).toBeInTheDocument();
  });

  it("does not render subtitle when not provided", () => {
    render(<ArcMeter value={30} label="Disk" />);
    expect(screen.queryByText("SSD")).not.toBeInTheDocument();
  });
});

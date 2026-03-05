Implement a dual-state visual status indicator component that displays for a total of 4 seconds. The indicator must support two states: "thinking" (displaying a brain icon) and "coding" (displaying a code icon). Use a single indicator component base that swaps only the icon based on the current processing state.

Requirements:
- Total display duration: 4 seconds
- Initial state: "thinking" with brain icon
- Auto-transition: After the thinking phase completes, automatically transition to "coding" state with code icon
- Final state: Indicator should either be dismissible by user interaction or auto-hide after the full 4-second duration
- Icon requirements: Brain and code icons must be visually distinct from each other and appropriately sized for the UI context
- Component architecture: Use the same indicator component base, only swapping the icon based on current processing state

Technical details to determine during implementation:
- Appropriate icon sizes for the UI context
- Duration split between thinking and coding phases (e.g., 2 seconds each, or configurable)
- Whether the indicator is dismissible via click/keypress or only auto-hides
- Visual styling (colors, animations, positioning) consistent with existing UI patterns

The implementation should be self-contained and ready to integrate into the existing codebase.
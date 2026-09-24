// cSpell:words genvar endgenerate endmodule
// HTM spatial-pooler permanence primitive. Packed lane 0 occupies the low bits.
// This module is separate from the legacy six-op mux in pivot_tile.
module pivot_permanence #(
    parameter LANES = 8,
    parameter COUNTER_WIDTH = 8
)(
    input  wire [LANES*COUNTER_WIDTH-1:0] permanence_in,
    input  wire [LANES-1:0] active,
    input  wire [COUNTER_WIDTH-1:0] increment,
    input  wire [COUNTER_WIDTH-1:0] decrement,
    output wire [LANES*COUNTER_WIDTH-1:0] permanence_out
);
    //synthesis translate_off
    initial begin
        if (LANES < 1 || COUNTER_WIDTH < 1)
            $fatal(1, "pivot_permanence requires positive dimensions");
    end
    // synthesis translate_on
    genvar lane;
    generate
        for (lane = 0; lane < LANES; lane = lane + 1) begin : update_lane
            wire [COUNTER_WIDTH-1:0] value;
            wire [COUNTER_WIDTH:0] sum;
            assign value = permanence_in[
                lane*COUNTER_WIDTH +: COUNTER_WIDTH
            ];
            assign sum = {1'b0, value} + {1'b0, increment};
            assign permanence_out[
                lane*COUNTER_WIDTH +: COUNTER_WIDTH
            ] = active[lane]
                ? (sum[COUNTER_WIDTH]
                    ? {COUNTER_WIDTH{1'b1}}
                    : sum[COUNTER_WIDTH-1:0])
                : ((value < decrement)
                    ? {COUNTER_WIDTH{1'b0}}
                    : value - decrement);
        end
    endgenerate
endmodule


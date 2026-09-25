module pivot_majority #(
    parameter WIDTH = 512,
    parameter N     = 8
)(
    input  wire                clk,
    input  wire                rst,
    input  wire [WIDTH-1:0]    operand_a,
    input  wire [WIDTH-1:0]    operand_b,
    input  wire [N*WIDTH-1:0]  vectors_in,
    output reg  [WIDTH-1:0]    majority_output
);
    // clk/rst/operand_b unused: majority votes bitwise across vectors_in,
    // using the low bits of operand_a as the vote threshold (count >= threshold => 1).
    // Assignment truncates or zero extends, including WIDTH < COUNT_WIDTH.
    wire [$clog2(N+1)-1:0] threshold = operand_a;

    integer bit_pos;
    integer vec_idx;
    integer count;

    always @(*) begin
        for (bit_pos = 0; bit_pos < WIDTH; bit_pos = bit_pos + 1) begin
            count = 0;
            for (vec_idx = 0; vec_idx < N; vec_idx = vec_idx + 1) begin
                if (vectors_in[vec_idx*WIDTH + bit_pos])
                    count = count + 1;
            end
            majority_output[bit_pos] = (count >= threshold);
        end
    end
endmodule

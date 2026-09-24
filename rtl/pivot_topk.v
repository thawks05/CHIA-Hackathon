module pivot_topk #(
    parameter WIDTH = 512,
    parameter N     = 8,
    parameter K     = 3
)
(
    input  wire                    clk,
    input  wire                    rst,
    input  wire [WIDTH-1:0]        operand_a,
    input  wire [WIDTH-1:0]        operand_b,
    input  wire [N*WIDTH-1:0]      vectors_in,
    output reg  [K*((N > 1) ? $clog2(N) : 1)-1:0]  topk_idx_output
);
    // clk/rst/operand_b unused: operand_a is the query vector.
    // topk_idx_output holds the indices (into vectors_in) of the K closest
    // vectors by Hamming distance, nearest first.
    localparam IDX_WIDTH = (N > 1) ? $clog2(N) : 1;
    localparam DIST_WIDTH = $clog2(WIDTH+1);

    // synthesis translate_off
    initial begin
        if (WIDTH < 1 || N < 1 || K < 1 || K > N)
            $fatal(1, "pivot_topk requires WIDTH>=1, N>=1, 1<=K<=N");
    end
    // synthesis translate_on

    integer vec_idx;
    integer bit_pos;
    integer k;
    integer j;
    integer best_idx;
    reg best_valid;
    reg [DIST_WIDTH-1:0] best_distance;
    reg [WIDTH-1:0] diff;
    reg [DIST_WIDTH-1:0] distance [0:N-1];
    reg     used      [0:N-1];

    always @(*) begin
        topk_idx_output = 0;
        for (vec_idx = 0; vec_idx < N; vec_idx = vec_idx + 1) begin
            diff = operand_a ^ vectors_in[vec_idx*WIDTH +: WIDTH];
            distance[vec_idx] = 0;
            for (bit_pos = 0; bit_pos < WIDTH; bit_pos = bit_pos + 1) begin
                distance[vec_idx] = distance[vec_idx] + diff[bit_pos];
            end
            used[vec_idx] = 1'b0;
        end

        for (k = 0; k < K; k = k + 1) begin
            best_idx = 0;
            best_valid = 1'b0;
            best_distance = {DIST_WIDTH{1'b1}};
            for (j = 0; j < N; j = j + 1) begin
                if (!used[j] && (!best_valid || distance[j] < best_distance)) begin
                    best_idx = j;
                    best_valid = 1'b1;
                    best_distance = distance[j];
                end
            end
            if (best_valid) begin
                used[best_idx] = 1'b1;
                topk_idx_output[k*IDX_WIDTH +: IDX_WIDTH] = best_idx;
            end
        end
    end
endmodule

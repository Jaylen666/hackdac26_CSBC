module bind_wrapper;
    // This module exists solely to give sby a top-level entry point.
    // The actual binding happens via the bind statement below.
endmodule

module sva_checker (
    input wire rst_ni,
    input wire prng_en_dis_inv_q,
    input wire clk_i
);

assert property (@(posedge clk_i) $rose(rst_ni) |-> (prng_en_dis_inv_q == '0));

endmodule


bind keymgr_ctrl sva_checker sva_checker_inst (.*);

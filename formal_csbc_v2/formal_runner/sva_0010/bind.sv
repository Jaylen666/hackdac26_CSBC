module bind_wrapper;
    // This module exists solely to give sby a top-level entry point.
    // The actual binding happens via the bind statement below.
endmodule

module sva_checker (
    input wire key_state_ecc_words_d,
    input wire rst_ni,
    input wire clk_i
);

assert property (@(posedge clk_i) disable iff (!rst_ni) !$isunknown(key_state_ecc_words_d));

endmodule


bind keymgr_ctrl sva_checker sva_checker_inst (.*);
